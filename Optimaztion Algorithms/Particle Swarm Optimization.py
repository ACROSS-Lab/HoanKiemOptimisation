import asyncio
import json
import os
import uuid

from typing import Dict, List
from asyncio import Future

import random
import time
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
reload_future: Future


async def message_handler(message):
    # print("received message:", message)
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
  
  
async def get_max_aqi(client, experiment_id):
    global expression_future
    expression_future = asyncio.get_running_loop().create_future()
    await client.expression(experiment_id, r"max_aqi")
    gama_response = await expression_future
    print("MAX_AQI =", gama_response["content"])
    return float(gama_response["content"])     


# Roads belonging to the initial solution
PHODIBO = [10, 11, 82, 132, 133, 158, 201, 202, 203, 271, 274, 276, 277, 279, 292, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 344, 425, 426, 427, 428, 540, 583, 585, 640]

# Total number of roads in the simulation
total_nb_road = 643

# Probability for a road to be closed in the initial swarm
# it will roughly correspond to the percentage of closed roads in the initial swarm
proba_closed_at_init = 0.1


class Particle:
    def __init__(self, position, velocity, fitness=0.0):
        self.position = position
        self.velocity = velocity
        self.bestPos = position
        self.bestFitness = fitness
        
        
async def initialize_swarm(N):
    swarm = []
    for _ in range(N):

        # Create a list of random boolean values, representing whether roads are closed or not
        roads_to_opt = [random.uniform(0.0, 1.0) < proba_closed_at_init for _ in range(total_nb_road)]

        # Combine PHODIBO with the randomly selected roads to form the particle's position
        position = [selected or i in PHODIBO for i, selected in enumerate(roads_to_opt)]

        velocity = [random.uniform(-1, 1) for _ in range(len(position))]

        fitness = await evaluate_fitness(position)
        
        particle = Particle(position, velocity, fitness)
        swarm.append(particle)
        
    return swarm


async def pso_optimization(max_iter, N, num_roads, w_start, w_end, c1, c2):
    swarm = await initialize_swarm(N)
    #TODO:pick the real best position/value
    
    best_fitness_swarm = float('inf')
    best_pos_swarm = swarm[0].position

    for iteration in range(max_iter):
        w = w_start - (w_start - w_end) * (iteration / max_iter)

        for particle in swarm:

            # Update velocity for each road to close
            for r in range(num_roads):
                r1, r2 = random.random(), random.random()
                particle.velocity[r] = (
                    w * particle.velocity[r] +
                    r1 * c1 * (1 if particle.bestPos[r] == particle.position[r] else -1) +
                    r2 * c2 * (1 if best_pos_swarm[r] == particle.position[r] else -1)
                )
    
            # Update position for each road to close
            for r in range(num_roads):
                if particle.velocity[r] > 0:
                    particle.position[r] = particle.position[r]
                else:
                    particle.position[r] = not particle.position[r]
            
            # Evaluate fitness (in this case, the air quality index) of the new position
            fitness = await evaluate_fitness(particle.position)

            # Update personal best
            if fitness < particle.bestFitness:
                particle.bestFitness = fitness
                particle.bestPos = particle.position

            # Update global best
            if fitness < best_fitness_swarm:
                best_fitness_swarm = fitness
                best_pos_swarm = particle.position

    # Return best particle of the swarm
    best_particle = min(swarm, key=lambda particle: particle.bestFitness)
    return best_particle


async def evaluate_fitness(position):
    global expression_future, step_future, reload_future

    id = str(uuid.uuid1())

    # Update the inital parameters(current_node) to a new parameters (new_params) by merging it with the list of adjacent
    new_params = [{"type": "list<int>", "name": "Closed roads", "value": [i for i, v in enumerate(position) if v]},
                  {"type": "string", "name": "Id", "value": id}]
    print("NEW_ROADS_SET =", new_params)
    
    # Load the GAMA model with the new parameters
    reload_future = asyncio.get_running_loop().create_future()
    await client.reload(experiment_id, new_params)
    res_reload = await reload_future
    if res_reload["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to reload the simulation", res_reload)
        return

    await run_GAMA_simulation(client, experiment_id)

    # # screenshoting
    # dir = str(Path(__file__).parents[1] / "Hoan Kiem Air Model" / "models" / "HKAM Data")
    # os.makedirs(dir, exist_ok=True)
    # name = id + ".png"
    # print("Saving a screenshot to", dir, name)
    # take_snapshot_command = r"save snapshot(world, 'my_display', {1000,1000}) to:'" + dir.replace('\\', '/') + "/" + name + "';"
    # expression_future = asyncio.get_running_loop().create_future()
    # print("expression", take_snapshot_command)
    # await client.expression(experiment_id, take_snapshot_command)
    # gama_response = await expression_future
    # if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
    #     print("Unable to save the display", gama_response)
    #     return

    return 1.0 / await get_max_aqi(client, experiment_id)


# Experiment and Gama-server constants
MY_SERVER_URL = "localhost"
MY_SERVER_PORT = 6868
GAML_FILE_PATH_ON_SERVER = str(Path(__file__).parents[1] / "Hoan Kiem Air Model" / "models" / "HKAM.gaml" ).replace('\\','/')
EXPERIMENT_NAME = "exp"


max_iter = 1000
N = 100
num_roads = 643
c1 = 2
c2 = 2
w_start = 0.9  # Starting inertia weight
w_end = 0.2    # Ending inertia weight


async def main():
    
    global experiment_future
    global client
    global experiment_id

    # Initial parameter
    MY_EXP_INIT_PARAMETERS = [{"type": "list<int>", "name": "Closed roads", "value": PHODIBO},
                              {"type": "string", "name": "Id", "value": "initial simulation"}]

    # Connect to the GAMA server
    client = GamaBaseClient(MY_SERVER_URL, MY_SERVER_PORT, message_handler)
    await client.connect(ping_interval = 30)

    # Load the model
    print("initialize a gaml model")
    experiment_future = asyncio.get_running_loop().create_future()
    await client.load(GAML_FILE_PATH_ON_SERVER, EXPERIMENT_NAME, False, False, False, True, MY_EXP_INIT_PARAMETERS)
    gama_response = await experiment_future

    # Get experiment id of the GAMA simulation in the model
    try:
        experiment_id = gama_response["content"]
    except Exception as e:
        print("error while initializing", gama_response, e)
        return
    
    # Start the timer
    start_time = time.time()

    best_particle = await pso_optimization(max_iter, N, num_roads, w_start, w_end, c1, c2)
    print("Best position:", best_particle.bestPos)
    print("Best fitness (air quality index):", best_particle.bestFitness)

    await kill_GAMA_simulation(client, experiment_id)
    
    # End the timer
    end_time = time.time()
    total_time = end_time - start_time
    print("Total time:", total_time, "seconds")

if __name__ == "__main__":
    asyncio.run(main())