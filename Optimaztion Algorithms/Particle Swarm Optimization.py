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

# # To run parallel code, source: https://stackoverflow.com/a/59385935
# import nest_asyncio
# nest_asyncio.apply()
#
# def background(f):
#     def wrapped(*args, **kwargs):
#         return asyncio.get_event_loop().run_in_executor(None, f, *args, **kwargs)
#
#     return wrapped

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
     # Run the GAMA simulation for n + 2 steps
    step_future = asyncio.get_running_loop().create_future()
    await client.step(experiment_id, 48*12, True)
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
    print("AQI =", gama_response["content"])
    return float(gama_response["content"])     


# Roads belonging to the initial solution
PhoDiBo_2023 = [0, 1, 2, 3, 6, 7, 8, 10, 11, 12, 13, 23, 24, 25, 26, 27, 28, 29, 82, 132, 133, 146, 158, 195, 196, 197, 198, 201, 202, 203, 215, 216, 217, 218, 219, 220, 221, 222, 271, 274, 276, 277, 279, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 315, 317, 318, 319, 320, 344, 346, 359, 360, 361, 362, 391, 397, 425, 426, 427, 428, 482, 483, 485, 540, 585, 640]

ROAD_CANT_CLOSE =  [30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 49, 97, 98, 174, 207, 208, 209, 210, 211, 212, 213, 214, 312, 313, 321, 322, 323, 324, 325, 326, 327, 328, 329, 330, 331, 332, 333, 334, 335, 336, 337, 377, 378, 379, 380, 381, 382, 383, 384, 385, 386, 387, 388, 389, 390, 409, 414, 415, 416, 417, 418, 
419, 420, 421, 431, 432, 433, 434, 435, 436, 437, 438, 439, 440, 441, 442, 445, 446, 451, 452, 453, 454, 455, 456, 457, 487, 488, 489, 519, 523, 524, 525, 526, 527, 528, 529, 531, 532, 533, 534, 535, 541, 544, 545, 546, 547, 548, 549, 586, 587, 588, 589, 590, 591, 592, 598, 599, 600, 601, 602, 616, 617, 631, 632, 633, 634, 635, 636, 641, 642]

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
    for i in range(N):
        # Create a list of random boolean values, representing whether roads are closed or not
        roads_to_opt = [random.uniform(0.0, 1.0) < proba_closed_at_init for _ in range(total_nb_road)]

        # Combine PHODIBO with the randomly selected roads to form the particle's position
        position = [selected or i in PhoDiBo_2023 for i, selected in enumerate(roads_to_opt)]

        velocity = [random.uniform(-1, 1) for _ in range(len(position))]

        fitness = await evaluate_fitness(position)

        particle = Particle(position, velocity, fitness)
        swarm.append(particle)

    return swarm


async def pso_optimization(max_iter, N, num_roads, w_start, w_end, c1, c2):
    swarm = await initialize_swarm(N)

    fitness_list = [p.bestFitness for p in swarm]
    best_fitness_swarm = min(fitness_list)
    best_pos_swarm = swarm[fitness_list.index(best_fitness_swarm)].position

    for iteration in range(max_iter):

        # prints a summary of the current swarm
        print("\n\n\nnew iteration:", iteration)

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
                if r in ROAD_CANT_CLOSE:
                    particle.position[r] = False
            
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

        print("whole swarm summary")
        for p in swarm:
            print("[" + ", ".join([str(i) for i, v in enumerate(p.position) if v]) + "]", p.bestFitness)
        print("current best fitness:", best_fitness_swarm, ",closed roads:",  [i for i, v in enumerate(best_pos_swarm) if v])

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


max_iter = 100
N = 7
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
    MY_EXP_INIT_PARAMETERS = [{"type": "list<int>", "name": "Closed roads", "value": PhoDiBo_2023},
                              {"type": "string", "name": "Id", "value": "initial simulation"}]

    # Connect to the GAMA server
    client = GamaBaseClient(MY_SERVER_URL, MY_SERVER_PORT, message_handler)
    await client.connect(ping_interval = None)

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