import asyncio
import json
from typing import Dict
from asyncio import Future

import math
from numpy import argmax
import random

from pathlib import Path

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

async def get_adjacent_roads(client, experiment_id, closed_roads):
    global expression_future
    expression_future = asyncio.get_running_loop().create_future()
    exp = r"adjacent_roads(" + str(closed_roads) + ")"
    await client.expression(experiment_id, exp)
    gama_response = await expression_future

    # Get "adjacent" of the current node
    adjacent = json.loads(gama_response["content"])
    print("ADJACENT_ROADS =", adjacent)
    return adjacent

async def run_GAMA_simulation(client, experiment_id):
    # 1 steps = 15 seconds
    # 4 steps = 1 minute
    # 240 steps = 1 hr
    # 5760 steps = 1 day 
    # 11520 steps = 1 weekend 
    # 40320 steps = 1 week
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

class Node:
    def __init__(self, closed_roads, parent = None):
        self.state = closed_roads
        self.visits = 0
        self.total_score = 0
        self.children = []
        self.parent = parent

async def mcts(client, experiment_id, initial_closed_roads, num_iterations):
    root = Node(initial_closed_roads)

    best_max_aqi = float('-inf')

    for _ in range(num_iterations):

        node = root

        # Selection
        while node.children:
            node = select_child(node)

        # Expansion
        if node.visits > 0:
            await expand_node(client, experiment_id, node)

        # Simulation
        simulation_aqi_result = await simulate(client, experiment_id, node.state)

        # Update best_max_aqi if a new maximum is found
        if simulation_aqi_result > best_max_aqi:
            best_max_aqi = simulation_aqi_result

        # Backpropagation
        backpropagate(node, simulation_aqi_result)

        # Stop condition: If the maximum AQI of the best child is > 100
        if best_max_aqi > 100:
            break

    best_child = select_best_child(root)
    return best_child, best_max_aqi


def select_child(node):
    exploration_constant = 1.4
    uct_values = [
            (child.total_score / child.visits) +
            math.sqrt((exploration_constant * 
            math.log(node.visits) / child.visits))
            if child.visits > 0 else float('inf')  # Handle division by zero
            for child in node.children
    ]
    return node.children[argmax(uct_values)]


async def apply_new_closed_roads(closed_roads, adj):
    new_closed_roads = [{"type": "list<int>", "name": "Closed roads", "value": closed_roads + adj}] 
    print("NEW_ROADS_SET =", new_closed_roads)
    return new_closed_roads

async def expand_node(client, experiment_id, node):
    adjacent = await get_adjacent_roads(client, experiment_id, node.state)
    for adj in adjacent:
        new_closed_roads = await apply_new_closed_roads(node.state, [adj])
        new_node = Node(new_closed_roads[0]["value"])
        node.children.append(new_node)

async def simulate(client : GamaBaseClient, experiment_id, closed_roads):
    adjacent = await get_adjacent_roads(client, experiment_id, closed_roads)
    one_adj = [random.choice(adjacent)]
    parameters = await apply_new_closed_roads(closed_roads, one_adj)
    # Load the GAMA model with the new parameters
    await client.reload(experiment_id, parameters)
    closed_roads = parameters[0]["value"]
    await run_GAMA_simulation(client, experiment_id)
    return await get_max_aqi(client, experiment_id)

def backpropagate(node, result):
    while node is not None:
        node.visits += 1
        node.total_score += result
        node = node.parent

def select_best_child(node):
    return max(node.children, key=lambda c: c.visits)


async def main():
    global experiment_future

    # Experiment and Gama-server constants
    MY_SERVER_URL = "localhost"
    MY_SERVER_PORT = 6868
    GAML_FILE_PATH_ON_SERVER =  str(Path(__file__).parents[0] / "MCTS model" / "models" / "model.gaml").replace('\\','/')
    EXPERIMENT_NAME = "exp"

    # Initial parameter
    initial_closed_roads = [10, 11, 82, 132, 133, 158, 201, 202, 203, 271, 274, 276, 277, 279, 292, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 344, 425, 426, 427, 428, 540, 583, 585, 640]
    MY_EXP_INIT_PARAMETERS = [{"type": "list<int>", "name": "Closed roads", "value": initial_closed_roads}]

    # Connect to the GAMA server
    client = GamaBaseClient(MY_SERVER_URL, MY_SERVER_PORT, message_handler)
    await client.connect(ping_interval = None)

    # Load the model
    print("initialize a gaml model")
    experiment_future = asyncio.get_running_loop().create_future()
    await client.load(GAML_FILE_PATH_ON_SERVER, EXPERIMENT_NAME, True, False, False, True, MY_EXP_INIT_PARAMETERS)
    gama_response = await experiment_future

    # Get experiment id of the GAMA simulation in the model
    try:
        experiment_id = gama_response["content"]
    except Exception as e:
        print("error while initializing", gama_response, e)
        return

    # Run the tree exploration algorithm to find the child node with the lowest max_aqi value
    num_iterations = 643
    best_child, best_max_aqi = await mcts(client, experiment_id, initial_closed_roads, num_iterations)
    print("Best set of closed roads:", best_child.state)
    print("Maximum AQI:", best_max_aqi)

    await kill_GAMA_simulation(client, experiment_id)


if __name__ == "__main__":
    asyncio.run(main())