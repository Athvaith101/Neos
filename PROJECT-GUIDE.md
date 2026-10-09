# NEOS, Explained Simply

This guide explains the whole NEOS project in plain language. It is written for someone who is new to energy software and programming.

## The most important thing to know

**NEOS is a computer model. It pretends to run a small electricity system so people can study what might happen.**

It does not read real electricity meters. It does not talk to real homes, batteries, solar panels, EV chargers, or a power company. It cannot turn equipment on or off.

Think of it like a flight simulator: it can show what a plane might do in a made-up flight, but it is not actually flying a passenger plane.

## 1. What NEOS is trying to study

A neighbourhood uses electricity. Some homes may have solar panels, batteries, and electric cars. Shops and important services also need power. The electricity wires and transformer can only carry so much.

NEOS asks questions such as:

- What happens if everyone uses electricity whenever they want?
- Could a simple time-based rule move some use to a better time?
- Could a computer plan the timing of flexible uses?
- What might happen if the weather changes, an outage happens, or the power network gets busy?
- How could a community battery help important services?

NEOS makes up a small example neighbourhood in the computer. It tries different plans and compares the results. The answer is only as realistic as the assumptions and equations in the model.

## 2. Important words

- **Backend:** The part of the program that does the work behind the website. NEOS's backend is a Python web service.
- **Frontend:** The pages and buttons that a person sees in a browser.
- **API:** A set of doors the frontend uses to ask the backend for information or work. The browser sends a request through a door, and the backend sends back an answer.
- **Simulation:** A pretend version of a real process, run by a computer.
- **Telemetry:** Measurements about a system, such as electricity use or battery charge. NEOS makes simulated measurements; it does not collect live ones.
- **Scenario:** A pretend situation to test, such as hot weather, cloudy weather, high EV charging, or a shortage.
- **Policy / mode:** The rule used to decide when flexible devices should use power.
- **Seed:** A number used to make random examples repeatable. If the settings and seed stay the same, a run can be repeated more easily.
- **Power flow:** A calculation that estimates how electricity moves through wires and what the voltage and equipment loading might be.
- **Evidence:** Saved results and records from experiments. Evidence describes what the model produced; it does not prove what would happen in a real neighbourhood.

## 3. Who talks to whom?

There are two different things that can be confused:

### Website communication

The browser talks to the NEOS backend API. It sends settings such as the number of homes and asks for a simulation. The backend sends the answer back to the browser.

This is computer-to-computer **API communication**.

### Energy measurements inside the model

The simulation creates pretend readings, such as household electricity use or battery charge. The simulated system may add pretend noise, delay, or missing readings to see how the model handles imperfect information.

These are **simulated telemetry readings**. They are not sent by real devices, and they do not mean the devices are communicating with one another.

There is no live connection to a utility, meter company, or device network in this project.

## 4. What happens in a normal NEOS run?

Here is the process in order:

1. **Choose the pretend neighbourhood.** The user sets the number of homes, EVs, solar sites, batteries, shops, and the transformer size.
2. **Build the pretend world.** NEOS creates computer objects for the homes, EVs, batteries, solar panels, shops, weather, and electricity network.
3. **Make pretend measurements.** The software creates readings for electricity use, solar production, and other model values. It can make readings imperfect to test the software.
4. **Estimate what is happening.** The program uses those readings to estimate the current state of the simulated neighbourhood.
5. **Guess what may happen next.** Forecast code estimates future electricity use, solar production, and EV needs. Forecasts are guesses, so the project also studies forecast errors and uncertainty.
6. **Keep a reserve.** Reserve code tries to keep some energy available for later needs. A reserve in the model is not a promise that a real battery has that energy.
7. **Pick a plan.** The controller chooses a schedule for flexible uses, such as when an EV might charge.
8. **Check the pretend network.** The grid model calculates whether voltages and equipment loading appear to stay within the model's chosen limits.
9. **Save what happened.** The run returns measurements, scores, time-series data, and records explaining the choices and checks.
10. **Show the answer.** The backend sends results to the website, which displays status, numbers, and charts.

The main run simulates a day in 15-minute steps. That means the computer advances through 96 pretend time periods.

## 5. The three main plans being compared

The urban grid page compares three ways to operate:

1. **Uncoordinated:** Each flexible device follows its own basic behaviour. They are not all planned together.
2. **Rule based / time of use:** A simple rule or electricity-price time period influences when devices use power.
3. **Coordinated:** The controller looks ahead using forecasts and tries to plan flexible use while respecting the model's device and grid limits.

The comparison helps show how the three plans behave under the chosen assumptions. It is not a guarantee that coordinated control would achieve the same result in a real neighbourhood.

## 6. The pages in the website

The website has separate pages so each part has a clear place.

### Home page — `/`

This is the setup page. It lets the user choose the size of the pretend urban neighbourhood: homes, EVs, solar sites, batteries, shops, transformer size, and grid solver.

It also lets the user set the backend API address. Most settings are saved in the browser so they can be reused on other pages. If the server requires an API key, the browser keeps it only for the current session.

### Grid page — `/grid.html`

This page runs the three urban plans and displays their results. It can show run progress, key numbers, time-series charts, flexibility estimates, decision records, a phase check, and a single power-flow check.

A comparison chart can be shown after both the coordinated and uncoordinated plans finish with the same scenario and replicate. The charts can be enlarged.

### Community page — `/community.html`

This page studies a pretend community energy hub. A shared battery may support homes and shops during a simulated outage. The user can change the community size, battery size, outage assumption, and random seed.

It also shows example offline messages and a simple islanding concept. These are demonstrations in software, not real community communications or electrical switching.

### Rural page — `/rural.html`

This page studies a pretend rural energy system. It includes settings for homes, feeder size, solar, battery size and power, weather/load type, random seed, and outage times.

The model includes examples of important and productive uses such as water pumps, water storage, cold storage, shops, clinic loads, streetlights, and work. The results are simulations, and the rural endpoint says benchmark validation is still pending.

### Utility page — `/utility.html`

This page demonstrates pretend utility requests, a communication outage, phase information, user requests, and an audit-style record of those requests.

The utility is not actually connected. A user request is only a simulated event. Even if the server accepts it, that does not mean an EV charger or any other device received a command.

### Backend and evidence page — `/evidence.html`

This page explains the workflow and lets the user request a single grid power-flow calculation, view module descriptions, and load saved evidence. It also lists commands for running checks and studies.

A single successful power-flow calculation is only one check. It does not mean every test passed or that all grid backends were compared successfully.

## 7. How the backend is arranged

The backend is the Python program that builds the model and answers website requests. The main web API is in `server.py`. It uses FastAPI and serves both data and the web pages.

The website asks for work through API routes. Examples:

- `/api/health` asks whether the service is responding.
- `/api/meta` asks what neighbourhood and solver settings are being used.
- `/api/scenarios` asks which pretend situations can be tested.
- `/api/run` runs one plan and returns its results.
- `/api/compare` runs and compares the plans.
- `/api/stream` sends progress while a run is happening.
- `/api/flexibility` returns estimated flexibility.
- `/api/decision_trace` returns records about the controller's choices.
- `/api/twin/powerflow` calculates one grid state.
- `/api/hub` runs the community battery example.
- `/api/rural` runs the rural example.
- `/api/discom` runs the pretend utility example.
- `/api/phase` calculates a pretend phase report.
- `/api/islanding` runs a concept-only islanding example.
- `/api/offline` returns example noticeboard and SMS text.
- `/api/override` puts a pretend user request in the ledger.
- `/api/overrides` reads the pretend request ledger.
- `/api/evidence` returns the saved `results.json` file.

The run page normally uses a live progress stream. If that connection cannot be used, the browser can try the ordinary run endpoint instead. The first run may take longer because the program has to prepare and fit its forecasting model.

## 8. What the main code folders do

The `neos/` folder contains the energy model pieces:

- **`config.py`:** Checks the neighbourhood settings and rejects values outside the allowed ranges.
- **`world.py`:** Creates the pretend homes, EVs, solar panels, batteries, shops, and network.
- **`dataset.py`:** Creates the synthetic history and data used by the model.
- **`telemetry.py`:** Creates pretend readings and models data problems such as noise, delay, and missing values.
- **`nwp.py`:** Creates simulated weather-forecast inputs.
- **`forecast.py`:** Predicts electricity use and solar production, and helps measure forecast uncertainty.
- **`service.py`:** Prepares shared model data, fits models, caches work, and starts runs.
- **`runner.py`:** Moves the simulation forward through the day and collects results.
- **`control.py` and `controllers.py`:** Decide schedules and apply the controller rules.
- **`grid.py`:** Calculates the simulated electrical network behaviour.
- **`resources.py`:** Describes what batteries, EVs, and other flexible resources can do.
- **`reserve.py`:** Estimates energy to keep back for future or important needs.
- **`flexibility.py`:** Makes the estimated flexibility records shown by the app.
- **`decision_trace.py` and `trace.py`:** Record what the model decided and why.
- **`ledger.py`:** Keeps simulated user-request and verification records.
- **`hub.py`:** Models the community energy hub.
- **`rural.py`:** Models the rural homes, services, resources, and schedule.
- **`discom.py`:** Models a pretend utility request and communication loss.
- **`phase.py`:** Examines how loads are spread among simulated electrical phases and suggests actions.
- **`offline.py`:** Creates example messages for places with poor internet.
- **`islanding.py`:** Demonstrates a simple islanding state machine, for learning only.
- **`platform.py`:** Tells the API and website which capabilities exist and their stated status.
- **`evidence.py`, `seeds.py`, and `claims.py`:** Help label results, record how they were produced, and connect claims with evidence.

Other important files:

- **`web/`:** The website's pages, styles, and browser JavaScript.
- **`tests/`:** Automated checks for pieces of the Python program.
- **`experiments.py`:** Runs a larger set of seeded research experiments and writes saved results.
- **`validate_backends.py`:** Compares the two grid calculation backends.
- **`results.json`:** Saved experiment results that the evidence endpoint can return.
- **`research/`:** Research notes and literature/claim comparisons.
- **`docs/` and `evidence/`:** Supporting project documents and evidence files, when present.
- **`build_static.py` and `build_web.py`:** Prepare website output.
- **`requirements.txt`:** Lists Python packages the backend needs.
- **`render.yaml`:** Describes how the Render-hosted service starts and checks health.
- **`README.md`, `AUDIT-STATUS.md`, `SPEC-TRACEABILITY.md`, `DEMO-SCRIPT.md`, `TOOLS-AND-FRAMEWORK.md`:** Other project guides, audit notes, feature tracking, demo instructions, and tool notes.

## 9. OpenDSS and the other grid solver

A grid solver is the part that estimates what happens to voltage and equipment loading when electricity is used.

NEOS has two options:

- **OpenDSS:** An established power-system calculation tool. NEOS can use it through the Python package `opendssdirect.py` when that package is installed and selected.
- **ReferenceGrid:** A simpler calculation written for this project, using NumPy. It provides a fallback and a way to compare results.

The `auto` setting chooses an available option. So the words “power-flow check” alone do not prove OpenDSS was used. Look at the solver selected in the API metadata. To check both implementations, run `validate_backends.py`. A single live point calculation is not the same as running that comparison.

The model checks voltage and equipment loading against limits set in the code. These are checks within the simulation. They are not a utility approval or a safety certificate.

## 10. What is implemented, simulated, or only an idea?

The project itself labels many features with status names. In simple terms:

- **Implemented module:** Code for a feature exists and is connected to the project. This does not mean it is connected to real equipment.
- **Implemented API:** A website/API route can return the feature's simulated information.
- **Simulation module:** A working model demonstration exists, but it is still pretend data and model behaviour.
- **Concept simulation:** A simplified example illustrates an idea; it is not a complete real-world system.
- **Designed but not integrated:** The idea or design exists, but it is not connected as a complete feature.

The current module-status response describes telemetry/state estimation, resources, reserve, offline participation, flexibility, traces, phase checks, community and rural models, ledger, utility requests, and islanding. It labels affordability and service charter as designed but not integrated. Read `/api/operating-system` for the exact status strings in the running version.

## 11. Tests and saved research results

A test is a small automatic check that helps catch programming mistakes. The README lists this command to run the Python tests:

```powershell
python -m unittest discover -s tests -t .
```

Other documented commands are:

```powershell
python validate_backends.py
python experiments.py
python build_static.py
python build_web.py
uvicorn server:app --port 8000
```

The backend comparison checks the grid solvers. The experiment script creates a larger study and updates `results.json`. The build commands prepare web files. The last command starts the local web service.

A command written in a guide is not proof that it has already been run. To say a test passed, someone needs to run it and inspect its result. Similarly, the browser's “run power flow” button makes one calculation; it does not run every test.

## 12. Running on Render

The repository has a `render.yaml` file describing a Render web service. It tells Render to install the Python packages, start the FastAPI application with Uvicorn, and check `/api/health).

When the website is deployed, the browser sends requests to the configured API address. A network, address, or server problem can stop a request. That would be a website-to-backend connection problem; it still would not be a connection to an electricity device.

## 13. What NEOS can and cannot prove

NEOS can help compare plans inside its model. It can show the model's forecasts, schedules, power-flow calculations, charts, and saved experiment results for stated settings.

NEOS cannot currently prove that:

- a real neighbourhood will use the same amount of electricity;
- a real battery or EV will follow a schedule;
- a real utility has received or accepted a request;
- a real grid will stay safe;
- an outage can be handled safely in the field;
- a customer will save a specific amount of money;
- the system is certified or ready to control equipment.

Before a real pilot, the project would need real meter and device connections, strong security and privacy controls, safe command/acknowledgement behaviour, local data, independent engineering checks, utility procedures, field testing, and required certifications. Those are separate steps beyond this simulation.

## 14. A short summary

NEOS has a multi-page website and a Python API. Together, they create pretend neighbourhoods, test different energy plans, calculate modelled grid behaviour, and show charts and records. The browser communicates with the NEOS API. Simulated telemetry is made inside the model. There is no live communication with real energy devices or a utility.

That is the right way to understand every result: **it tells us what NEOS's model calculated under its assumptions, not what a real neighbourhood has measured or will definitely do.**
