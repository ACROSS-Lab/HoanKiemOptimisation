import asyncio
import json
import uuid
from typing import Dict
from asyncio import Future

from gama_client.base_client import GamaBaseClient
from gama_client.command_types import CommandTypes
from gama_client.message_types import MessageTypes

import random
import time
import os
from pathlib import Path

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
    global step_future, expression_future
    print("Running the experiment")
    
    # Run the GAMA simulation for n + 2 steps (2 blank steps for initialization prob)
    step_future = asyncio.get_running_loop().create_future()
    await client.step(experiment_id, 11520 + 2, True)
    gama_response = await step_future
    if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to execute the experiment", gama_response)
        return

    # # screenshoting
    # dir = str(Path(__file__).parents[0] / "results")
    # os.makedirs(dir, exist_ok=True)
    # name = "-".join([str(road) for road in closed_roads]) + ".png"
    # print("Saving a screenshot to", dir, name)
    # take_snapshot_command = r"save snapshot('my_display') to:'" + dir.replace('\\', '/') + "/" + name + "';"
    # expression_future = asyncio.get_running_loop().create_future()
    # await client.expression(experiment_id, take_snapshot_command)
    # gama_response = await expression_future
    # if gama_response["type"] != MessageTypes.CommandExecutedSuccessfully.value:
    #     print("Unable to save the display", gama_response)
    #     return


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


# Define the async function for GAMA simulation
async def reload_gama_simulation(individual):
    global expression_future, step_future, reload_future

    # Update the inital parameters(current_node) to a new parameters (new_params) by merging it with the list of adjacent
    new_params = [{"type": "list<int>", "name": "Closed roads", "value": [i for i,v in enumerate(individual) if v]},
                  {"type": "string", "name": "Id", "value": str(uuid.uuid1())}]
    print("NEW_ROADS_SET =", new_params)
    
    # Load the GAMA model with the new parameters
    reload_future = asyncio.get_running_loop().create_future()
    await client.reload(experiment_id, new_params)
    res_reload = await reload_future
    if res_reload["type"] != MessageTypes.CommandExecutedSuccessfully.value:
        print("Unable to reload the simulation", res_reload)
        return
    await run_GAMA_simulation(client, experiment_id)
    return await get_max_aqi(client, experiment_id)  


# Number of individuals in each generation
POPULATION_SIZE = 1000

PHODIBO = [10, 11, 82, 132, 133, 158, 201, 202, 203, 271, 274, 276, 277, 279, 292, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 344, 425, 426, 427, 428, 540, 583, 585, 640]
PHODIBO_BOOLEAN = [True] * len(PHODIBO)
remain_roads = [x for x in range(643) if x not in PHODIBO]


async def cal_fitness(gnome):
    # Get the AQI for the current set of closed roads (chromosome)
    max_aqi = await reload_gama_simulation(gnome)
    # Calculate fitness as the inverse of AQI (lower AQI is better)
    fitness = 1.0 / max_aqi
    return fitness


class Individual(object):
    '''
    Class representing individual in population
    '''
    def __init__(self, chromosome, fitness = 0):
        self.chromosome = chromosome
        self.fitness = fitness
    
    
    @classmethod
    def mutated_genes(self):
        '''
        create random genes for mutation
        '''
        gene = [random.choice([True, False]) for _ in range(len(remain_roads))]
        return gene


    @classmethod
    def create_gnome(self):
        '''
        create chromosome or string of genes
        '''
        remaining_genes = self.mutated_genes()
        return PHODIBO_BOOLEAN + remaining_genes


    async def mate(self, par2):
        '''
        Perform mating and produce new offspring
        '''

        # chromosome for offspring
        child_chromosome = []
        for gp1, gp2 in zip(self.chromosome, par2.chromosome):	

            # random probability
            prob = random.random()

            # if prob is less than 0.45, insert gene
            # from parent 1
            if prob < 0.45:
                child_chromosome.append(gp1)

            # if prob is between 0.45 and 0.90, insert
            # gene from parent 2
            elif prob < 0.90:
                child_chromosome.append(gp2)

            # otherwise insert random gene(mutate),
            # for maintaining diversity
            else:
                child_chromosome.append(self.mutated_genes())

        fitness = await cal_fitness(child_chromosome)
        # create new Individual(offspring) using
        # generated chromosome for offspring
        return Individual(child_chromosome, fitness)



# Experiment and Gama-server constants
MY_SERVER_URL = "localhost"
MY_SERVER_PORT = 6868
GAML_FILE_PATH_ON_SERVER = str(Path(__file__).parents[1] / "Hoan Kiem Air Model" / "models" / "HKAM.gaml" ).replace('\\','/')
EXPERIMENT_NAME = "exp"


# Driver code
async def main():
    
    global experiment_future
    global client
    global experiment_id
 
    # Initial parameter
    MY_EXP_INIT_PARAMETERS = [{"type": "list<int>", "name": "Closed roads", "value": PHODIBO},
                                {"type": "string", "name": "Id", "value": str(uuid.uuid1())}]

    # Connect to the GAMA server
    client = GamaBaseClient(MY_SERVER_URL, MY_SERVER_PORT, message_handler)
    await client.connect(ping_interval = None)

    # Load the model
    print("Initializing GAMA model")
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
    
    global POPULATION_SIZE
 
    #current generation
    generation = 1

    found = False
    population = []
    
    # Initialize variables for tracking generations without significant improvement
    max_generations_without_improvement = 10
    generations_without_improvement = 0
    significant_margin = 10
    previous_best_fitness = None
 
    # create initial population
    for _ in range(POPULATION_SIZE):
        gnome = Individual.create_gnome()
        fitness = await cal_fitness(gnome)
        ind = Individual(gnome, fitness)
        population.append(ind)


    while not found:

        # sort the population in increasing order of fitness score
        population = sorted(population, key = lambda x:x.fitness)
  
        # If the best fitness value remains within the significant margin for a number of generations, break the loop
        if previous_best_fitness is not None and abs(previous_best_fitness - population[0].fitness) <= significant_margin:
            generations_without_improvement += 1
        else:
            generations_without_improvement = 0

        if generations_without_improvement >= max_generations_without_improvement:
            print("No significant improvement for {} generations. Exiting the loop.".format(max_generations_without_improvement))
            break

        # Update the previous best fitness value
        previous_best_fitness = population[0].fitness

        # Otherwise generate new offsprings for new generation
        new_generation = []

        # Perform Elitism, that mean 10% of fittest population
        # goes to the next generation
        s = int((10*POPULATION_SIZE)/100)
        new_generation.extend(population[:-s])

        # From 50% of fittest population, Individuals
        # will mate to produce offspring
        s = int((90*POPULATION_SIZE)/100)
        for _ in range(s):
            parent1 = random.choice(population[:s])
            parent2 = random.choice(population[:s])
            child = await parent1.mate(parent2)
            new_generation.append(child)

        population = new_generation

        print("Generation: {}\tRoads Set: {}\tFitness: {}".format(
        generation,
        [i for i,v in enumerate(population[0].chromosome) if v],  # Convert integers to strings
        population[0].fitness
        ))

        generation += 1


    print("Generation: {}\tRoads Set: {}\tFitness: {}".format(
        generation,
        [i for i,v in enumerate(population[0].chromosome) if v],  # Convert integers to strings
        population[0].fitness
    ))
 
    await kill_GAMA_simulation(client, experiment_id)
 
    # End the timer
    end_time = time.time()
    total_time = end_time - start_time
    print("Total time:", total_time, "seconds")

if __name__ == "__main__":
    asyncio.run(main())
