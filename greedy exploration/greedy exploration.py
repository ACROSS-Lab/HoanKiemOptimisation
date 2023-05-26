import asyncio
import json
from typing import Dict
from asyncio import Future
from pathlib import Path
import time

from gama_client.base_client import GamaBaseClient
from gama_client.command_types import CommandTypes
from gama_client.message_types import MessageTypes


experiment_future: Future
play_future: Future
pause_future: Future
expression_future: Future
step_future: Future
stop_future: Future


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


async def get_max_aqi(client, experiment_id):
    global expression_future
    expression_future = asyncio.get_running_loop().create_future()
    await client.expression(experiment_id, r"max_aqi")
    gama_response = await expression_future
    print("MAX_AQI =", gama_response["content"])
    return float(gama_response["content"])


async def get_closed_roads(client, experiment_id):
    global expression_future
    expression_future = asyncio.get_running_loop().create_future()
    await client.expression(experiment_id, r"closed_roads")
    gama_response = await expression_future
    print("CLOSED_ROADS =", gama_response["content"])
    return json.loads(gama_response["content"])


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


# 1 steps = 15 seconds
# 4 steps = 1 minute
# 240 steps = 1 hr
# 5760 steps = 1 day 
# 11520 steps = 1 weekend 
# 40320 steps = 1 week


async def GAMA_sim(client, experiment_id):
    global step_future
    print("Running the experiment")
     # Run the GAMA simulation for n + 2 steps
    step_future = asyncio.get_running_loop().create_future()
    await client.step(experiment_id, 11520 + 2, True)
    gama_response = await step_future
    if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to execute the experiment", gama_response)
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


async def child_node(client: GamaBaseClient, experiment_id, current_node, adjacent_roads):
    global expression_future, step_future, stop_future

    # Update the inital parameters(current_node) to a new parameters (new_params) by merging it with the list of adjacent
    breakpoint()
    new_params = [{"type": "list<int>", "name": "Closed roads", "value": current_node + adjacent_roads}]
    print("NEW_ROADS_SET =", new_params)

    # Load the GAMA model with the new parameters
    await client.reload(experiment_id, new_params)

    await GAMA_sim(client, experiment_id)
    closed_roads = await get_closed_roads(client, experiment_id)
    max_aqi = await get_max_aqi(client, experiment_id)

    return {"max_aqi": max_aqi, "closed_roads": closed_roads}


async def tree_exploration(client: GamaBaseClient, experiment_id, current_node):
    # Run the GAMA simulation and get the list of closed_roads and max_aqi
    await GAMA_sim(client, experiment_id)
    closed_roads = await get_closed_roads(client, experiment_id)
    max_aqi = await get_max_aqi(client, experiment_id)

    while True:
        # Call a function in GAMA to get a list of adjacent roads to the input roads
        adjacent = await get_adjacent_roads(client, experiment_id, current_node)

        # Generate child nodes and explore them recursively
        child_nodes = []
        for adj in adjacent:
            child_nodes.append(await child_node(client, experiment_id, current_node, [adj]))

        # Find the child node with the lowest max_aqi for further exploration
        lowest_child = min(child_nodes, key=lambda x: x["max_aqi"])

        # If the child with the lowest max_aqi has a higher max_aqi than the max_aqi of the current node, stop exploration
        if lowest_child["max_aqi"] > max_aqi:
            print("Stopping exploration")
            print("CLOSED_ROADS =", closed_roads)
            print("MAX_AQI =", max_aqi)
            return
        
        # If all elements is iterated in the current list adjacent, the last child of the exploration with the lowest max_aqi is updated to be the current node, do the tree_exploration function again to get another list of adjacent to that current node, start exploring again
        if not adjacent:
            current_node = lowest_child["closed_roads"]
            await tree_exploration(client, experiment_id, current_node)
            return

        # Print the closed_roads and max_aqi of the child node with the lowest max_aqi in the tree and explore it
        print("Exploring child node with lowest max_aqi:")
        print("CLOSED_ROADS =", lowest_child["closed_roads"])
        print("MAX_AQI =", lowest_child["max_aqi"])

        current_node = lowest_child["closed_roads"]
        await tree_exploration(client, experiment_id, current_node)


async def main():
    global experiment_future

    # Experiment and Gama-server constants
    MY_SERVER_URL = "localhost"
    MY_SERVER_PORT = 6868

    GAML_FILE_PATH_ON_SERVER = str(Path(__file__).parents[0] / "greedy model" / "models" / "model.gaml").replace('\\','/') 
    
    EXPERIMENT_NAME = "exp"

    # Initial parameter
    root_node = [10, 11, 82, 132, 133, 158, 201, 202, 203, 271, 274, 276, 277, 279, 292, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 344, 425, 426, 427, 428, 540, 583, 585, 640]
    MY_EXP_INIT_PARAMETERS = [{"type": "list<int>", "name": "Closed roads", "value": root_node}]

    # Connect to the GAMA server
    client = GamaBaseClient(MY_SERVER_URL, MY_SERVER_PORT, message_handler)
    await client.connect(ping_interval = None)

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

    # Run the tree exploration algorithm to find the child node with the lowest max_aqi value
    await tree_exploration(client, experiment_id, root_node)

    await kill_GAMA_simulation(client, experiment_id)

    # End the timer
    end_time = time.time()
    total_time = end_time - start_time
    print("Total time:", total_time, "seconds")


if __name__ == "__main__":
    asyncio.run(main())