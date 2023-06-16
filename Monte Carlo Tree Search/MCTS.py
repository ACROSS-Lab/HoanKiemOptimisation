import time
import math
import random
from pathlib import Path

import asyncio
import json
from typing import Dict
from asyncio import Future

from gama_client.base_client import GamaBaseClient
from gama_client.command_types import CommandTypes
from gama_client.message_types import MessageTypes

experiment_future: Future
expression_future: Future
step_future: Future
stop_future: Future

async def message_handler(message):
    print("received message:", message)
    if "command" in message:
        if message["command"]["type"] == CommandTypes.Load.value:
            experiment_future.set_result(message)
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

    # Get "adjacent" roads to the current set of closed roads
    adjacent = json.loads(gama_response["content"])
    print("ADJACENT_ROADS =", adjacent)
    return adjacent

async def new_closed_roads(closed_roads, adj):
    new_closed_roads = [{"type": "list<int>", "name": "Closed roads", "value": closed_roads + adj}] 
    print("NEW_ROADS_SET =", new_closed_roads)
    return new_closed_roads

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

async def run_GAMA_simulation(client, experiment_id):
    # 1 steps = 15 seconds
    # 4 steps = 1 minute
    # 240 steps = 1 hr
    # 5760 steps = 1 day 
    # 11520 steps = 1 weekend 
    # 40320 steps = 1 week
    global step_future
    print("Running the experiment")
    # Run the GAMA simulation for n + 2 steps (2 blank steps for initialization prob)
    step_future = asyncio.get_running_loop().create_future()
    await client.step(experiment_id, 11520 + 2, True)
    gama_response = await step_future
    if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to execute the experiment", gama_response)
        return

async def randomPolicy(state):
    while not state.isTerminal():
        try:
            action = random.choice(await state.getPossibleActions())
        except IndexError:
            raise Exception("Non-terminal state has no possible actions: " + str(state))
        state, terminal_max_aqi = await state.takeAction(action)

    return state.getReward(terminal_max_aqi = terminal_max_aqi)

class treeNode():
    def __init__(self, state, parent, max_aqi):
        self.state = state
        self.isTerminal = state.isTerminal()
        self.isFullyExpanded = self.isTerminal
        self.parent = parent
        self.numVisits = 0
        self.totalReward = 0
        self.children = {}
        self.max_aqi = max_aqi

    def __str__(self):
        s=[]
        s.append("totalReward: %s"%(self.totalReward))
        s.append("numVisits: %d"%(self.numVisits))
        s.append("isTerminal: %s"%(self.isTerminal))
        s.append("possibleActions: %s"%(self.children.keys()))
        return "%s: {%s}"%(self.__class__.__name__, ', '.join(s))

class MCTS():
    def __init__(self, client, experiment_id, timeLimit, iterationLimit, explorationConstant,
                 rolloutPolicy=randomPolicy):
        if timeLimit != None:
            if iterationLimit != None:
                raise ValueError("Cannot have both a time limit and an iteration limit")
            # time taken for each MCTS search in milliseconds
            self.timeLimit = timeLimit
            self.limitType = 'time'
        else:
            if iterationLimit == None:
                raise ValueError("Must have either a time limit or an iteration limit")
            # number of iterations of the search
            if iterationLimit < 1:
                raise ValueError("Iteration limit must be greater than one")
            self.searchLimit = iterationLimit
            self.limitType = 'iterations'
        self.explorationConstant = explorationConstant
        self.rollout = rolloutPolicy
        self.client = client
        self.experiment_id = experiment_id

    async def search(self, initialState, root_max_aqi, needDetails=False):
        self.root = treeNode(initialState, parent = None, max_aqi = root_max_aqi)

        if self.limitType == 'time':
            timeLimit = time.time() + self.timeLimit / 1000
            while time.time() < timeLimit:
                await self.executeRound()
        else:
            for _ in range(self.searchLimit):
                await self.executeRound()

        bestChild = self.getBestChild(self.root, 0)
        action=(action for action, node in self.root.children.items() if node is bestChild).__next__()
        if needDetails:
            return {"action": action, "expectedReward": bestChild.totalReward / bestChild.numVisits}
        else:
            return action

    async def executeRound(self):
        """
            execute a selection-expansion-simulation-backpropagation round
        """
        node = await self.selectNode(self.root)
        reward = await self.rollout(node.state)
        self.backpropogate(node, reward)

    async def selectNode(self, node):
        while not node.isTerminal:
            if node.isFullyExpanded:
                node = self.getBestChild(node, self.explorationConstant)
            else:
                return await self.expand(node)
        return node

    async def expand(self, node):
        actions = await node.state.getPossibleActions()
        for action in actions:
            if action not in node.children:
                newState, child_max_aqi = await node.state.takeAction(action)
                newNode = treeNode(newState, node, max_aqi = child_max_aqi)
                node.children[action] = newNode
                if len(actions) == len(node.children):
                    node.isFullyExpanded = True
                return newNode

        raise Exception("Should never reach here")

    def backpropogate(self, node, reward):
        while node is not None:
            node.numVisits += 1
            node.totalReward += reward
            node = node.parent

    def getBestChild(self, node, explorationValue):
        bestValue = float("-inf")
        bestNodes = []
        for child in node.children.values():
            nodeValue = child.totalReward / child.numVisits + explorationValue * math.sqrt(
                2 * math.log(node.numVisits) / child.numVisits)
            if nodeValue > bestValue:
                bestValue = nodeValue
                bestNodes = [child]
            elif nodeValue == bestValue:
                bestNodes.append(child)
        return random.choice(bestNodes)

class ClosedRoads():
    def __init__(self, client, experiment_id, initial_closed_roads, root_max_aqi):
        self.state = initial_closed_roads
        self.client = client
        self.experiment_id = experiment_id
        self.root_max_aqi = root_max_aqi

    async def getPossibleActions(self):
        possibleActions = await get_adjacent_roads(self.client, self.experiment_id, self.state)
        return possibleActions
    
    async def takeAction(self, action):
        newState = await new_closed_roads(self.state, [action])
        await self.client.reload(self.experiment_id, newState)
        await run_GAMA_simulation(self.client, self.experiment_id)
        max_aqi = await get_max_aqi(self.client, self.experiment_id)
        # return newState as an object, max_aqi
        return ClosedRoads(self.client, self.experiment_id, newState[0]["value"], self.root_max_aqi), max_aqi

    def isTerminal(self):
        # Closing max 50 roads
        if len(self.state) == 50:
            return True

    def getReward(self, terminal_max_aqi):
        return self.root_max_aqi - terminal_max_aqi

async def main():
    global experiment_future

    # Experiment and Gama-server constants
    MY_SERVER_URL = "localhost"
    MY_SERVER_PORT = 6868
    GAML_FILE_PATH_ON_SERVER = str(Path(__file__).parents[0] / "MCTS model" / "models" / "HKmodel_MCTS.gaml").replace('\\','/')
    EXPERIMENT_NAME = "exp"

    # Initial parameter
    # Pedestrian area (Phố đi bộ Hồ Hoàn Kiếm)
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

    # Start the timer
    start_time = time.time()

    await run_GAMA_simulation(client, experiment_id)
    root_max_aqi = await get_max_aqi(client, experiment_id)

    initialState = ClosedRoads(client = client, 
                               experiment_id = experiment_id,
                               initial_closed_roads = initial_closed_roads,
                               root_max_aqi = root_max_aqi)
    
    explorationConstant = 1 / math.sqrt(2)

    searcher = MCTS(client = client, 
                    experiment_id = experiment_id, 
                    timeLimit = None, 
                    iterationLimit = 1000,
                    explorationConstant = explorationConstant)
    
    action = await searcher.search(initialState = initialState, 
                                   root_max_aqi = root_max_aqi, 
                                   needDetails = True)

    print("Best_closed_roads: ", action)

    await kill_GAMA_simulation(client, experiment_id)

    # End the timer
    end_time = time.time()
    total_time = end_time - start_time
    print("Total time:", total_time, "seconds")

if __name__ == "__main__":
    asyncio.run(main())