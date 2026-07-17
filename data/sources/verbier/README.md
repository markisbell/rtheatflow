# Open-DHN data

This dataset contains average sensor measurements of a District Heating Network (DHN). Details on how the data was obtained can be found in [1].

The reference network is the meshed DHN of Verbier, an alpine resort in the Swiss Alps. The network is powered by 2 heating plants (HS0 and HS1) and serves 150 substations (S0-S149).


##  Content


```
opendhn-data/
  -data/
    -mass_flow.csv
    -power.csv
    -return_temperature.csv
    -supply_temperature.csv
  -network/
    -heating_stations.csv
    -nodes.csv
    -pipes.csv
    -substations.csv
  -README.md

```

The folder `data/` contains the CSV files with the measurements available for each heating plant (HS) and substation (S):

* `data/mass_flow.csv`: mass flow in kg/s
* `data/power.csv`: power in W
* `data/return_temperature.csv`: return temperature in °C, corresponding to the *inlet* temperature for the heating plants and to the *outlet* temperature for substations
* `data/supply_temperature.csv`: supply temperature in °C, corresponding to the *outlet* temperature for the heating plants and to the *inlet* temperature for substations

The folder `network/` contains the CSV files with the topology and characteristics of the network, which is modelled as a graph where heating plants, substations and pipes are edges:

* `network/nodes.csv`: list of nodes in the graph with their IDs and coordinates
* `network/heating_stations.csv`: list of heating plants, their IDs and inlet and outlet nodes
* `network/substations.csv`: list of substations, their IDs and inlet and outlet nodes
* `network/pipes.csv`: list of pipes, their IDs and inlet and outlet nodes and well as characteristics. A comprehensive description of the entries is given in the following table:

| **Variable** | **Data type** | **Description**                                             | **Unit** |
|--------------|---------------|-------------------------------------------------------------|----------|
| pipe\_id     | string        | Unique identifier of the pipe.                              | -        |
| startpoint   | string        | Unique identifier of the starting node.                     | -        |
| endpoint     | string        | Unique identifier of the ending node.                       | -        |
| is\_supply   | boolean       | Boolean indicating if the pipe pertains to the supply line. | -        |
| is\_aerial   | boolean       | Boolean indicating if the pipe is an aerial pipe.           | -        |
| length       | float         | Lenght of the pipe in meters.                               | m        |
| d\_int       | float         | Diameter of the internal pipe in meters.                    | m        |
| t\_int       | float         | Thickness of the internal pipe in meters.                   | m        |
| t\_ins       | float         | Thickness of the insulation in meters.                      | m        |
| t\_ext       | float         | Thickness of the external casing in meters.                 | m        |
| lambda\_ins  | float         | Thermal conductivity of insulation.                         | W/(K·m)  |
| roughness    | float         | Roughness of the internal surface of the pipe.              | mm       |


##  Benchmark

A benchmark using this dataset was proposed in [1]. Further information, as well as the code for reproducing the results, can be found in the [benchmark repository](https://gitlab.idiap.ch/eguzki/opendhn).


## Citing

If this work was useful for you, please cite:

Boghetti, R., Kämpf, J. H. 2023. A benchmark for the simulation of meshed district heating networks based on anonymised monitoring data. CISBAT 2023.


## Authors

* [Roberto Boghetti](https://www.idiap.ch/~rboghetti/)
* [Jérôme Kämpf](https://www.idiap.ch/~jkaempf/)

## License

The data is released under the Creative Commons Attribution 4.0 (CC-BY-4.0) license.

## Acknowledgments

We would like to thank [Altis](https://www.altis.swiss/) for providing us the necessary data and agreeing to share this benchmark.

We would also like to thank Patrick Dewarrat and Giona Galizia ([RWB](https://www.rwbgroupe.ch/)) and [Giuseppe Peronato](https://www.giuseppeperonato.com/) for the early help in obtaining the data and necessary permissions.


## Funding acknowledgement

This benchmark has been developed in the framework of the [Eguzki research project](https://www.aramis.admin.ch/Kategorien/?ProjectID=47432&Sprache=en-US), funded by the Swiss Federal Office of Energy, OIKEN SA, ALTIS Groupe SA, SATOM SA and RWB Valais SA.


<img src="resources/figures/eu-emblem.jpg" width="80" height="54" align="left" alt="EU emblem" />
This project has received funding from the European Union’s Horizon 2020 research and innovation programme under the Marie Skłodowska-Curie grant agreement No. 945363.


## References

[1] Boghetti, R., Kämpf, J. H. 2023. A benchmark for the simulation of meshed district heating networks based on anonymised monitoring data. CISBAT 2023.