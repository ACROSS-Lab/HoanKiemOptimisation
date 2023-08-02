/**
* Name: Stripped
* Based on the internal empty template. 
* Author: truon
* Tags: 
*/


model BatchTestAQI2

import "agents/traffic.gaml"
import "agents/pollution.gaml"
import "agents/visualization.gaml"

global
{
	
	float time_per_4cycles;
	bool benchmark <- true;
	bool closed <- false;
	float step <- 15#s;
	float max_aqi;
	float min_aqi;
	float mean_aqi;
	
	// Load shapefiles
	string resources_dir <- "../includes/bigger_map/";
	shape_file roads_shape_file <- shape_file(resources_dir + "roads.shp");
	shape_file dummy_roads_shape_file <- shape_file(resources_dir + "small_dummy_roads.shp");
	shape_file buildings_shape_file <- shape_file(resources_dir + "buildings.shp");
	shape_file road_cells_shape_file <- shape_file(resources_dir + "road_cells.shp");
	shape_file naturals_shape_file <- shape_file(resources_dir + "naturals.shp");
	shape_file buildings_admin_shape_file <- shape_file(resources_dir + "buildings_admin.shp");
	
	geometry shape <- envelope(buildings_shape_file);
	list<road> open_roads;
	list<int> closed_roads;
	list<pollutant_cell> active_cells;


	init 
	{		
		create road from: roads_shape_file {}
		
		open_roads <- list(road);
		map<road, float> road_weights <- road as_map (each::each.shape.perimeter); 
		road_network <- as_edge_graph(road) with_weights road_weights;
		geometry road_geometry <- union(road accumulate (each.shape));
		active_cells <- pollutant_cell overlapping road_geometry;

		original_network <- as_edge_graph(road) with_weights road_weights;
		
		//Visualization
		create building from: buildings_shape_file 
		{
			p_cell <- pollutant_cell closest_to self;
		}
		
		create decoration_building from: buildings_admin_shape_file;
		create dummy_road from: dummy_roads_shape_file;
		create natural from: naturals_shape_file;
		create progress_bar with: [x::2550, y::1300, width::500, height::100, max_val::500, title::"Cars",  left_label::"0", right_label::"500"];
		create progress_bar with: [x::2550, y::1650, width::500, height::100, max_val::1500, title::"Motorbikes", left_label::"0", right_label::"1500"];
		create line_graph_aqi with: [x::2500, y::2000, width::1100, height::500, label::"Hourly AQI"];
		create param_indicator with: [x::2500, y::2803, size::30, name::"Time", value::"00:00:00", with_box::true, width::1100, height::200];		
		
		// Init pollutant cells (Not Sure if needed)
		create road_cell from: road_cells_shape_file 
		{
			neighbors <- road_cell at_distance 10#cm;
			affected_buildings <- building at_distance 50 #m;
		}
	}
	
	
	action update_vehicle_population(string type, int delta) {
		list<vehicle> vehicles <- vehicle where (each.type = type);
		if (delta < 0) {
			ask -delta among vehicle {
				do die;
			}
		} else {
			create vehicle number: delta with: [type::type];
		}
	}
	
	
	reflex update_car_population {
		int delta_cars <- n_cars - vehicle count (each.type = "car");
		do update_vehicle_population("car", delta_cars);
		ask first(progress_bar where (each.title = "Cars")) {
			do update(float(n_cars));
		}
	}
	
	
	reflex update_motorbike_population {
		int delta_motorbikes <- n_motorbikes - vehicle count (each.type = "motorbike");
		do update_vehicle_population("motorbike", delta_motorbikes);
		ask first(progress_bar where (each.title = "Motorbikes")) {
			do update(float(n_motorbikes));
		}
	}
   			
   			
   	action adjacent_roads (list<int> input_roads){
   		list<int> adjacent <- [];
   		loop i over: input_roads{
   			adjacent <- adjacent + connected_roads(road(i)) collect int(each);
   		}
   		adjacent <- remove_duplicates(adjacent);
   		return adjacent - input_roads;
	}
	
	
   	list<road> connected_roads(road a_road){
		return list<road>(
			remove_duplicates(
			[	source_of(original_network, a_road), 
				target_of(original_network, a_road)
			] 
			accumulate 
   			(out_edges_of(original_network, each)  +  in_edges_of(original_network, each) ))
   			) 
   			- a_road;}


	reflex update_open_roads 
	{
		ask road {
			if (closed_roads contains int(self)) {
				closed <- true;
			}
			else {
				closed <- false;
			}
		}

		open_roads <- road where ! each.closed;
		
		map<road, float> road_weights <- open_roads as_map (each::each.shape.perimeter); 
		graph new_road_network <- as_edge_graph(open_roads) with_weights road_weights;
		ask vehicle {
			recompute_path <- true;
		}
		road_network <- new_road_network;
		
	}
	
	
	reflex create_congestions {
		ask open_roads {
			list<vehicle> vehicles_on_road <- vehicle at_distance 1;
			int n_cars_on_road <- vehicles_on_road count (each.type = "car");
			int n_motorbikes_on_road <- vehicles_on_road count (each.type = "motorbike");
			do update_speed_coeff(n_cars_on_road, n_motorbikes_on_road);
		}
		map<float, float> road_weights <- open_roads as_map (each::(each.shape.perimeter / each.speed_coeff));
		road_network <- road_network with_weights road_weights;
	}
	
	
	matrix<float> mat_diff <- matrix([
		[pollutant_diffusion,pollutant_diffusion,pollutant_diffusion],
		[pollutant_diffusion, (1 - 8 * pollutant_diffusion) * pollutant_decay_rate, pollutant_diffusion],
		[pollutant_diffusion,pollutant_diffusion,pollutant_diffusion]]);

		
	reflex produce_pollutant {
		// Absorb pollutants emitted by vehicles
		ask active_cells parallel: true {
			list<vehicle> vehicles_in_cell <- vehicle inside self;
			loop v over: vehicles_in_cell {
				if (is_number(v.real_speed)) {
					float dist_traveled <- v.real_speed * step / #km;
	
					co <- co + dist_traveled * EMISSION_FACTOR[v.type]["CO"];
					nox <- nox + dist_traveled * EMISSION_FACTOR[v.type]["NOX"];
					so2 <- so2 + dist_traveled * EMISSION_FACTOR[v.type]["SO2"];
				    pm <- pm + dist_traveled * EMISSION_FACTOR[v.type]["PM"];
				}
			}
		}

		
		// Diffuse pollutants to neighbor cells
		diffuse var: co on: pollutant_cell matrix: mat_diff;
		diffuse var: nox on: pollutant_cell matrix: mat_diff;
		diffuse var: so2 on: pollutant_cell matrix: mat_diff;
		diffuse var: pm on: pollutant_cell matrix: mat_diff;
	}
	
	
	reflex calculate_aqi when: 
	//every(1 #cycle) { 
	//every(1 #minute) {
	every(refreshing_rate_plot) {
		max_aqi <- max(pollutant_cell accumulate each.aqi);
		min_aqi <- min(pollutant_cell accumulate each.aqi);
		mean_aqi <- mean(pollutant_cell accumulate each.aqi);
		ask line_graph_aqi {
		 	do update(max_aqi);
		 }
		ask indicator_health_concern_level {
		 	do update(max_aqi);
		 }
	}
	
	
	reflex update_building_aqi {
		ask building parallel: true {
			aqi <- pollutant_cell(p_cell).aqi;
		}
	}
	
	reflex save_results when: ( time > 0 )and every(4 #cycle) {
		save 
		(
			"nb_closed_roads"   +   length(closed_roads)	+ ": " +
			"closed roads: " 	+ 	string(closed_roads) 	+ "; " +
			"cycle: " 			+ 	string(cycle) 			+ "; " + 
			"max_aqi: " 		+ 	max_aqi					+ "; " + 
			"mean_aqi: " 		+  	mean_aqi 
		) 
		to: "/results/Greedydata.txt" format: text rewrite: false;
		
//		save [closed_roads, cycle, max_aqi, mean_aqi] to: "/results/Greedydata.csv" format: "csv" rewrite: false header: true  ;
	} 
	
	reflex benchmark when: benchmark and every(4 #cycle) {
		float start <- machine_time;
//		write "MAX AQI: " + max_aqi;
//		write "MEAN AQI: " + mean_aqi;
		
//		time_per_4cycles <- machine_time - start;
//		write "time per 4cycles: " + time_per_4cycles;
//		write "MIN AQI: " + min_aqi;
		
//		list<vehicle> vehicles_in_cell <- vehicle inside self;
//		write "Max Speed: " + max(vehicles_in_cell accumulate each.real_speed);
//		write "Mean Speed: " + mean(vehicles_in_cell accumulate each.real_speed);
//		write "Min Speed: " + min(vehicles_in_cell accumulate each.real_speed);
		
//		write length(open_roads);
//		write length(road);
	}
}


experiment exp autorun: false{
	parameter "Number of motorbikes" var: n_motorbikes <- 200 min: 0 max: 1000;
	parameter "Number of cars" var: n_cars <- 5 min: 75 max: 500;
	parameter "Refreshing time plot" var: refreshing_rate_plot init: 1#mn min:1#mn max: 1#h;
	parameter "Closed roads" var: closed_roads <- [10, 11, 82, 132, 133, 158, 201, 202, 203, 271, 274, 276, 277, 279, 292, 302, 303, 304, 305, 306, 307, 308, 309, 310, 311, 344, 425, 426, 427, 428, 540, 583, 585, 640];
	float seed <- 0.9314761131573912;
	
//	reflex save_simulation when: ( time > 0 ) {	
//		write "Save of simulation : " + save_simulation('/results/greedysim.gsim');				
//	}
	
	output{
		display my_display type: opengl background: #black{
			species vehicle;
			species road;
			species natural;
			species building;
			species decoration_building;

			species dummy_road;
			species progress_bar;
			//species param_indicator;
			
			species line_graph_aqi;
			species indicator_health_concern_level;
		}
	}
}







