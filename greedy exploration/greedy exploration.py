import asyncio
import json
import os
from datetime import datetime
from typing import Dict, List
from asyncio import Future
import time
from pathlib import Path
import igraph as ig
import matplotlib.pyplot as plt

from gama_client.base_client import GamaBaseClient
from gama_client.command_types import CommandTypes
from gama_client.message_types import MessageTypes


experiment_future: Future
play_future: Future
pause_future: Future
expression_future: Future
step_future: Future
stop_future: Future
reload_future: Future


async def message_handler(message):
    print("received message:", message)
    if "command" in message:
        if message["command"]["type"] == CommandTypes.Load.value:
            experiment_future.set_result(message)
        elif message["command"]["type"] == CommandTypes.Play.value:
            play_future.set_result(message)
        elif message["command"]["type"] == CommandTypes.Pause.value:
            pause_future.set_result(message)
        elif message["command"]["type"] == CommandTypes.Expression.value:
            expression_future.set_result(message)
        elif message["command"]["type"] == CommandTypes.Step.value:
            step_future.set_result(message)
        elif message["command"]["type"] == CommandTypes.Stop.value:
            stop_future.set_result(message)
        elif message["command"]["type"] == CommandTypes.Reload.value:
            reload_future.set_result(message)


async def get_max_aqi(client, experiment_id):
    global expression_future
    expression_future = asyncio.get_running_loop().create_future()
    await client.expression(experiment_id, r"max_aqi")
    gama_response = await expression_future
    print("MAX_AQI =", gama_response["content"])
    return float(gama_response["content"])


async def get_adjacent_roads(client, experiment_id, current_node):
    global expression_future
    expression_future = asyncio.get_running_loop().create_future()
    exp = r"adjacent_roads(" + str(current_node) + ")"
    await client.expression(experiment_id, exp)
    gama_response = await expression_future

    # Get "adjacent" of the current node
    adjacent = json.loads(gama_response["content"])
    print("ADJACENT_ROADS =", adjacent)
    return adjacent


async def GAMA_sim(client, experiment_id, closed_roads):
    # 1 steps = 15 seconds
    # 4 steps = 1 minute
    # 240 steps = 1 hr
    # 5760 steps = 1 day 
    # 11520 steps = 1 weekend 
    # 40320 steps = 1 week
    global step_future
    global expression_future

    print("Running the experiment")
    # Run the GAMA simulation for n + 2 steps (2 blank steps for initialization prob)
    step_future = asyncio.get_running_loop().create_future()
    await client.step(experiment_id, 11520 + 2, True)
    gama_response = await step_future
    if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to execute the experiment", gama_response)
        return

    # screenshoting
    dir = str(Path(__file__).parents[0] / "results")
    os.makedirs(dir, exist_ok=True)
    name = "-".join([str(road) for road in closed_roads]) + ".png"
    print("Saving a screenshot to", dir, name)
    take_snapshot_command = r"save snapshot('my_display') to:'" + dir.replace('\\', '/') + "/" + name + "';"
    expression_future = asyncio.get_running_loop().create_future()
    await client.expression(experiment_id, take_snapshot_command)
    gama_response = await expression_future
    if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to save the display", gama_response)
        return




async def kill_GAMA_simulation(client, experiment_id):
    global stop_future
    print("killing the GAMA simulation")
    # Kill the GAMA simulation
    stop_future = asyncio.get_running_loop().create_future()
    await client.stop(experiment_id)
    gama_response = await stop_future
    if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to stop the experiment", gama_response)
        return

count = -1 #nto start at 0

def get_id() -> int:
    global count
    count += 1
    return count

class Node:
    def __init__(self, closed_roads: List[int], parent=None):
        self.state: List[int] = closed_roads
        self.children: List[Node] = []
        self.parent: Node = parent
        self.aqi: int = 0
        self.id: int = get_id()

    def get_root(self) -> ig.Graph:
        if self.parent:
            return self.parent.get_root()
        return ig.Graph(directed=True)

    def to_graph(self, current_graph: ig.Graph) -> ig.Vertex:
        root = self.get_root() if current_graph is None else current_graph
        v = root.add_vertex(self.id)
        v["state"]  = self.state
        v["id"]     = self.id
        for c in self.children:
            child_vertex = c.to_graph(root)
            root.add_edge(v.index, child_vertex.index)
        return v


def refresh_plot(root: Node, current_node: Node, ax, save_to_file: bool):
    graph = root.to_graph(None).graph
    plt.cla()
    ig.plot(
        graph,
        target=ax,
        layout="kk",
        vertex_size=0.5,
        vertex_color=["green" if g_id == root.id else "red" if g_id == current_node.id else "steelblue" for g_id in
                      graph.vs["id"]],
        vertex_frame_width=4.0,
        vertex_frame_color="white",
        vertex_label=[str(v_st[-1]) for v_st in graph.vs["state"]],
        vertex_label_size=10.0,
    )
    plt.pause(0.1)
    if save_to_file:
        plt.savefig("exploration/" + str(datetime.now().strftime("%Y-%m-%d %Hh%M %Ssec")) + ".png")


async def child_node(client: GamaBaseClient, experiment_id, current_node: Node, adjacent_roads):
    global expression_future, step_future, stop_future, reload_future

    # Update the inital parameters(current_node) to a new parameters (new_params) by
    # merging it with the list of adjacent
    # breakpoint()
    new_closed_roads = current_node.state + adjacent_roads

    #removing duplicates
    new_closed_roads = list(dict.fromkeys(new_closed_roads))

    #sorting
    new_closed_roads.sort()

    new_params = [{"type": "list<int>", "name": "Closed roads", "value": new_closed_roads}]
    print("NEW_ROADS_SET =", new_params)

    # Load the GAMA model with the new parameters
    reload_future = asyncio.get_running_loop().create_future()
    await client.reload(experiment_id, new_params)
    res_reload = await reload_future
    if res_reload["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to reload the simulation", res_reload)
        return

    await GAMA_sim(client, experiment_id, new_closed_roads)
    max_aqi = await get_max_aqi(client, experiment_id)

    return {"max_aqi": max_aqi, "closed_roads": new_closed_roads}


async def greedy_exploration(client: GamaBaseClient, experiment_id, current_node: Node, root: Node, ax):
    # Run the GAMA simulation and get the list of closed_roads and max_aqi
    await GAMA_sim(client, experiment_id, current_node.state)

    max_aqi = await get_max_aqi(client, experiment_id)
    current_node.aqi = max_aqi

    # Plot the tree/graph (toggle comment)
    # refresh_plot(root, current_node, ax, True)

    while True:
        # Call a function in GAMA to get a list of adjacent roads to the input roads
        adjacent = await get_adjacent_roads(client, experiment_id, current_node.state)

        # Generate child nodes and explore them recursively
        for adj in adjacent:
            child = await child_node(client, experiment_id, current_node, [adj])
            child_n = Node(child["closed_roads"])
            child_n.aqi = child["max_aqi"]
            current_node.children += [child_n]

        # Find the child node with the lowest max_aqi for further exploration
        lowest_child = min(current_node.children, key=lambda x: x.aqi)

        # If the child with the lowest max_aqi has a higher max_aqi than the max_aqi of
        # the current node, stop exploration
        if lowest_child.aqi > max_aqi:
            print("Stopping exploration")
            print("CLOSED_ROADS =", current_node.state)
            print("MAX_AQI =", max_aqi)
            return lowest_child
        
        # If all elements is iterated in the current list adjacent, the last child of
        # the exploration with the lowest max_aqi is updated to be the current node,
        # call the greedy_exploration function again to get another list of adjacent to
        # that current node, start exploring again
        if not adjacent:
            return await greedy_exploration(client, experiment_id, lowest_child, root, ax)

        # Print the closed_roads and max_aqi of the child node with the lowest max_aqi in the graph and explore it
        print("Exploring child node with lowest max_aqi:")
        print("CLOSED_ROADS =", lowest_child.state)
        print("MAX_AQI =", lowest_child.aqi)

        return await greedy_exploration(client, experiment_id, lowest_child, root, ax)

async def main():
    global experiment_future

    # Experiment and Gama-server constants
    MY_SERVER_URL = "localhost"
    MY_SERVER_PORT = 6868

    GAML_FILE_PATH_ON_SERVER = str(Path(__file__).parents[0] / "greedy model" / "models" / "HKmodel_greedy.gaml").replace('\\','/') 
    
    EXPERIMENT_NAME = "exp"

    # Initial parameter
    # Pedestrian area (Phố đi bộ Hồ Hoàn Kiếm)
    root_node = [10, 11, 82, 132, 133, 158, 201, 202, 203, 271, 274, 276, 277, 279, 292, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 344, 425, 426, 427, 428, 540, 583, 585, 640]
    MY_EXP_INIT_PARAMETERS = [{"type": "list<int>", "name": "Closed roads", "value": root_node}]
    root = Node(root_node)

    # Connect to the GAMA server
    client = GamaBaseClient(MY_SERVER_URL, MY_SERVER_PORT, message_handler)
    await client.connect(ping_interval=None)

    # initialise a screen to plot the graph
    ax = plt.subplots()

    # Load the model
    print("initialize a gaml model")
    print(GAML_FILE_PATH_ON_SERVER)
    experiment_future = asyncio.get_running_loop().create_future()
    await client.load(GAML_FILE_PATH_ON_SERVER, EXPERIMENT_NAME, True, False, False, True, MY_EXP_INIT_PARAMETERS)
    gama_response = await experiment_future

    # Get experiment id of the GAMA simulation in the model
    try:
        experiment_id = gama_response["content"]
    except Exception as e:
        print("error while initializing", gama_response, e)
        return

    # Start the timer
    start_time = time.time()

    # Run the greedy exploration algorithm to find the child node with the lowest max_aqi value
    leaf = await greedy_exploration(client, experiment_id, root, root, ax)

    await kill_GAMA_simulation(client, experiment_id)

    refresh_plot(root, leaf, ax, True)

    # End the timer
    end_time = time.time()
    total_time = end_time - start_time
    print("Total time:", total_time, "seconds")

if __name__ == "__main__":
    asyncio.run(main())