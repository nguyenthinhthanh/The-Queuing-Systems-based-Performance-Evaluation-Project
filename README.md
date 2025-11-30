# The Queuing Systems-based Performance Evaluation Project

## Overview
This project implements a **queuing system simulator** using **SimPy**, a Python-based discrete-event simulation framework.  
The system models a wide range of **single-queue and multi-queue configurations**, evaluates performance metrics, and demonstrates how real-world systems can be abstracted using **Kendall’s notation** (e.g., M/M/1, M/M/c/K).

The project includes both the simulation engine and a set of experiments that compare **theoretical results** with **simulation-based observations**, providing insight into queue behavior under different loads.

---

## Table of Contents
1. [Overview](#overview)  
2. [Objectives](#objectives)  
3. [Architecture Docs](#architecture-docs)  
4. [System Design](#system-design)  
5. [Mapping Real-world Systems](#mapping-from-real-world-to-queuing-model)  
6. [Experiments & Evaluation](#experiments--evaluation)  
7. [Results](#results)  
8. [Technologies](#technologies)  
9. [How to Run](#how-to-run)  
10. [References](#references)  
11. [License](#license)

---

## Objectives
- Implement queuing systems following **Kendall’s notation**:  
  `A / B / m / K / n / D`.
- Support multiple queue configurations:
  - `M/M/1`
  - `M/M/1/K`
  - `M/M/n`
  - `M/M/n/n` (Erlang Loss System)
  - `M/M/c/K`
- Enable simulation of **queuing networks** (series, parallel, multi-stage).
- Compare simulation outcomes with **analytical formulas**.
- Provide mapping between **real-world systems** and their **queuing models**.

---

## Architecture Docs
The project is organized into modules that separate core simulation responsibilities:

- **Arrival Generator** – produces customers using Poisson processes or custom inter-arrival times.  
- **Queue / Server Module** – manages service, waiting lines, and capacity constraints.  
- **Logger & Metrics Engine** – records timestamps and computes performance statistics (waiting time, queue length, utilization, loss, throughput).

Additional Jupyter notebooks provide:

- Visualization tools  
- Analytical model comparisons  
- Experimental setups for reproducibility  

---

## System Design

### Reference Architecture
The simulator is built around three essential components:

1. **Arrival Event**  
   Generates customer arrivals following exponential or user-defined distributions.

2. **Service Facility (Servers)**  
   Handles multiple servers (`m`), exponential service times, and finite or infinite queue capacity.

3. **Logger / Metrics Module**  
   Records performance data such as:
   - Waiting time  
   - Service time  
   - Queue length  
   - Utilization  
   - Loss probability  

---

## Simulation Flow

Simulation proceeds through time-ordered events:

- `init()` → Setup environment  
- `arrival()` → Handle new incoming customer  
- `beginService()` → Assign server when available  
- `endService()` → Release server and log event  
- `schedule()` → Maintain event timeline  

---

## Mapping from Real-world to Queuing Model

| Real-world Scenario        | Queuing Model  | Description                              |
|----------------------------|----------------|------------------------------------------|
| CPU scheduling (multicore) | M/M/c network  | Each core acts as a separate server      |
| Multi-hop router chain     | M/M/1 → M/M/c/K| Each router represents a queue node      |
| Hospital or clinic workflow| M/M/c/K        | Multiple doctors; limited waiting area   |
| Cloud microservices        | M/M/n          | Worker threads handle requests           |
| Call center                | M/M/c          | Multiple agents serving calls            |

---

## Experiments & Evaluation

Experiments investigate:

- Average waiting time  
- Average queue length  
- Server utilization  
- Throughput  
- Loss probability (for finite-K systems)  

Validation performed using:

- Little’s Law  
- Steady-state analytical solutions for M/M/1 and M/M/c  
- Erlang-B and Erlang-C formulas  

Workloads include:

- Light traffic (ρ < 0.5)  
- Heavy traffic (ρ → 1)  
- Overloaded scenarios (ρ > 1)  

---

## Results

Key observations include:

- Queue congestion increases sharply near **ρ ≈ 1**.  
- Multi-server queues (**M/M/c**) significantly reduce average delay under heavy load.  
- Finite-capacity models (**M/M/c/K**) exhibit **blocking/loss** when arrival rate exceeds capacity.  
- Simulation results match theoretical values, confirming model correctness.  

Plots illustrate:

- Queue length over time  
- Waiting-time distribution  
- Utilization vs. load  
- Loss probability curves  

---

## Technologies

- **Language:** Python 3.x  
- **Simulation Library:** SimPy  
- **Visualization:** Matplotlib, Pandas  
- **Environment:** Jupyter Notebook  

---

## How to Run

### 1. Install dependencies
```bash
pip install simpy matplotlib pandas jupyter
```
### 2. Open the notebook
```bash
jupyter notebook Assignment1_SPE.ipynb
```

### 3. Modify simulation parameters

Adjust the following variables to explore different queuing system configurations:

- **Arrival rate (λ)**
- **Service rate (μ)**
- **Number of servers (c)**
- **Queue capacity (K)**

These parameters enable experimentation with models such as **M/M/1**, **M/M/c**, and **M/M/c/K**.

---

## References

- **SimPy Documentation** – https://simpy.readthedocs.io/  
- **Kleinrock, L.** *Queueing Systems, Volume I: Theory*  
- **Gross & Harris** – *Fundamentals of Queueing Theory*  

---

## License

This repository is intended for **academic and educational purposes only**.



