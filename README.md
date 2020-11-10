NATSimTools
===========

Example how to run:
1. Run simulation:
`python simulation.py --benchmark -s poisson -o poisson.txt`
You can choose another strategy with -s, e.g. simple, ij, i2j.
2. Process simulation results:
`python dataproc.py poisson.txt [...other strategies genarated files]`
