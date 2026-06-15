# Cyberwave — Guida Completa per LLM (Zero → Full Knowledge)

> **Scopo di questo file**: Un LLM che legge questo documento deve poter passare da zero contesto a conoscenza operativa completa della piattaforma Cyberwave. Non è un riassunto, è una mappa operativa.

---

## 🔗 DOVE GUARDARE SUBITO

| Risorsa | URL | Cosa trovi |
|---------|-----|-----------|
| **LLMs.txt (PARTIRE DA QUI)** | https://docs.cyberwave.com/llms.txt | Indice completo di TUTTE le pagine docs — formato LLM |
| **Quickstart** | https://docs.cyberwave.com/overview | Setup iniziale, primo robot in 5 min |
| **Python SDK** | https://docs.cyberwave.com/sdks/python-sdk | API Python completa |
| **SDK Reference** | https://docs.cyberwave.com/sdks/python-sdk-reference | Riferimento metodi/classi |
| **API Reference** | https://docs.cyberwave.com/api-reference/overview | REST API + MQTT + WebRTC |
| **MCP Server** | https://docs.cyberwave.com/sdks/mcp | Integrazione AI assistant ↔ Robot |
| **Architecture** | https://docs.cyberwave.com/feature-reference/architecture/architecture | Cloud-Edge topology |
| **AI Features** | https://docs.cyberwave.com/feature-reference/ai/overview | VLM, NLP→Action, Fleet |
| **NLP → Robot Action** | https://docs.cyberwave.com/feature-reference/ai/natural-language-to-robot-action | Core AI feature |
| **Catalog** | https://cyberwave.com/catalog | Tutti i robot/asset disponibili |
| **Robotic Arms** | https://docs.cyberwave.com/overview/robotic-arms | SO-ARM100/SO101 |
| **Robotic Dogs** | https://docs.cyberwave.com/overview/robotic-dogs | Unitree Go2 |
| **Drones** | https://docs.cyberwave.com/overview/drones | DJI Tello/Mini |
| **Hardware Go2** | https://docs.cyberwave.com/overview/hardware/unitree-go2 | Specifiche Go2 |
| **Hardware SO101** | https://docs.cyberwave.com/overview/hardware/so-arm100 | Specifiche SO101 |
| **Workflows** | https://docs.cyberwave.com/overview/features/workflows | Pipeline AI agent |
| **GitHub SDK** | https://github.com/cyberwave-os/cyberwave-python | Codice sorgente SDK |
| **GitHub Edge** | https://github.com/cyberwave-os/cyberwave-edge-python | SDK Edge (Jetson/RPi) |
| **GitHub Catalog** | https://github.com/cyberwave-os/Catalog | URDF e definizioni robot |

---

## 🏗️ COS'È CYBERWAVE

Cyberwave è una piattaforma di **Physical AI Infrastructure**. In pratica:

```
[Il tuo codice Python] → [Cyberwave SDK] → [Digital Twin cloud] → [Simulazione O Hardware reale]
```

**Filosofia core**: "Abstract with Digital Twins, plug in AI, and ship to production."

**Cosa fa concretamente**:
1. **Astrae l'hardware** → Scrivi codice una volta, gira su qualsiasi robot
2. **Digital Twins** → Repliche virtuali dei robot nel cloud
3. **Simulazione** → Testa il tuo codice senza hardware fisico
4. **Same API sim↔real** → `cw.affect("simulation")` o `cw.affect("live")` per switchare
5. **AI Workflows** → Pipeline dichiarative: percezione → ragionamento → azione
6. **MCP Server** → Un LLM può controllare robot direttamente
7. **VLM Pipeline** → Vision-Language Models integrati nativamente

---

## 🧱 ARCHITETTURA (4 Piani)

```
[Experience Layer]  ←→  [Control Plane (Cloud)]  ←→  [Data Plane (Transport)]  ←→  [Edge Plane (Device)]
   Next.js UI              Django Backend                  MQTT Broker                   Robot Agent
   three.js 3D             Orchestrator                    WebRTC Signaling              ROS2/MavLink Bridge
                            PostgreSQL/TimescaleDB
```

### Tre protocolli di comunicazione

| Protocollo | Uso | Latenza |
|-----------|-----|---------|
| **REST API** | CRUD: gestione twin, ambienti, configurazione | Alta (non real-time) |
| **MQTT** | "Sistema nervoso" — telemetria, joint states, comandi | Bassa (real-time) |
| **WebRTC** | Video streaming P2P (bypassa il cloud) | Molto bassa |

### Cosa sta dove

| Componente | Tecnologia | Scopo |
|-----------|-----------|-------|
| Backend | Django + `django-ninja` | REST API, identity, asset registry |
| Orchestrator | Custom | Fleet config, mission state, OTA updates |
| Data Lake | PostgreSQL / TimescaleDB | Telemetria storica, log missioni |
| MQTT Broker | MQTT | Telemetria e comandi real-time |
| WebRTC Signaling | WebRTC | Video P2P |
| Edge Agent | Python o C++ | Processo leggero su hardware robot |
| Bridges | Adapters (ROS2, MavLink) | Traduce comandi Cyberwave → driver locali |
| Frontend | Next.js | Dashboard utente |
| Visualizer | WebGL (three.js) | Rendering Digital Twin real-time |

---

## ⚙️ SETUP & INSTALLAZIONE

### Installazione SDK
```bash
# Core
pip install cyberwave

# Con supporto camera
pip install cyberwave[camera]

# Con Intel RealSense
pip install cyberwave[realsense]

# Con ML (machine learning)
pip install cyberwave[ml]

# Oppure con uv (consigliato)
uv add cyberwave
```

### Autenticazione
```bash
# Metodo 1: Variabile d'ambiente (consigliato)
export CYBERWAVE_API_KEY=your_api_key_here

# Windows PowerShell
$env:CYBERWAVE_API_KEY = "your_api_key_here"
```

```python
# Metodo 2: In codice
from cyberwave import Cyberwave
cw = Cyberwave(api_key="your_api_key_here")

# Metodo 3: Da env var (default)
cw = Cyberwave()  # usa CYBERWAVE_API_KEY
```

**Dove prendere la key**: Dashboard → Profile → API Tokens

### Requisiti
- Python ≥ 3.10 (nel progetto: ≥ 3.13)
- Pacchetto `cyberwave >= 0.5.0`

---

## 🧠 CONCETTI CHIAVE

| Concetto | Cosa è | Identificatore |
|----------|--------|---------------|
| **Digital Twin** | Replica virtuale cloud di un robot fisico | `twin_id` |
| **Asset** | Modello robot nel catalog (URDF + metadata) | Slug: `vendor/model` |
| **Environment** | Mondo 3D virtuale dove i twin vivono | `environment_id` |
| **Simulation** | Sessione di esecuzione dell'environment | `session_id` |
| **Workflow** | Pipeline AI: percezione → ragionamento → azione | `workflow_id` |
| **Observation** | Dati dal robot (sensori, camera, joint states) | — |
| **Action** | Comandi al robot (muovi, gripper, vola) | — |
| **Edge Agent** | Processo leggero sul robot fisico | — |

### Flusso fondamentale
```
1. Crea Digital Twin dal catalog (slug: "vendor/model")
2. Crea Environment
3. Piazza il Twin nell'Environment
4. Scegli modalità: cw.affect("simulation") o cw.affect("live")
5. Loop: Observe → Think (AI) → Act
6. Stessa API funziona su sim e hardware reale
```

---

## 📦 PYTHON SDK — Riferimento Operativo

### Inizializzazione
```python
from cyberwave import Cyberwave
cw = Cyberwave()
```

### ⚡ Asset Slugs per i Robot dell'Hackathon

| Robot | Slug | Tipo |
|-------|------|------|
| SO-ARM100 (SO101) | `the-robot-studio/so101` | Braccio robotico 6-DOF |
| Unitree Go2 | `unitree/go2` | Cane robot quadrupede |
| DJI Mini 4 Pro | `dji/dji-mini-4-pro` | Drone |
| DJI Tello | `dji/tello` | Drone (entry-level) |

### Creare/Ottenere un Digital Twin
```python
# Metodo principale: usa slug vendor/model
robot = cw.twin("the-robot-studio/so101")    # braccio
dog = cw.twin("unitree/go2")                  # cane robot
drone = cw.twin("dji/dji-mini-4-pro")        # drone
```

### Ricerca nel Catalog
```python
# Cercare asset disponibili
results = cw.assets.search("unitree")
results = cw.assets.search("arm")
```

### Modalità Simulazione vs Live
```python
# Switchare tra simulazione e hardware reale
cw.affect("simulation")   # comandi → twin virtuale
cw.affect("live")          # comandi → hardware fisico
```

---

### 🦾 Controllo Joint (Bracci Robotici)

```python
# Ottenere twin
robot = cw.twin("the-robot-studio/so101")

# Listare joint disponibili
joints = robot.joints.list()

# Impostare singolo joint
robot.joints.set("joint_name", 45, degrees=True)

# Impostare singolo joint (metodo alternativo)
robot.set_joint(...)

# Impostare più joint con dizionario
robot.set_joints({"joint_1": 0.5, "joint_2": -0.2})
```

### 🐕 Locomozione (Cani Robot / Rover)

```python
dog = cw.twin("unitree/go2")

# Comandi di movimento
dog.move_forward()
dog.move_forward(distance=0.5)
dog.move_backward()
dog.turn_left()
dog.turn_right()

# Posizionamento assoluto
dog.edit_position(...)
dog.edit_rotation(...)
```

### 🚁 Droni

```python
drone = cw.twin("dji/dji-mini-4-pro")

# Decollo
drone.takeoff()

# Movimento
drone.move_forward(distance=1.0)
drone.turn_left()

# Atterraggio (metodo specifico da verificare in docs)
```

### 📷 Camera & Streaming

```python
# Avviare streaming video
robot.start_streaming()

# Catturare un frame (ritorna numpy array)
frame = robot.capture_frame()

# Ottenere ultimo frame
frame = robot.get_latest_frame()
```

---

### 🔄 Workflows (Pipeline AI Agent)

```python
# Creare un workflow
workflow = cw.workflows.create(
    name="pick-and-place",
    steps=[
        {
            "type": "perception",
            "model": "object-detection",
            "input": "camera"
        },
        {
            "type": "reasoning",
            "model": "gpt-4",
            "prompt": "Given detected objects, plan a pick sequence"
        },
        {
            "type": "action",
            "command": "pick_object",
            "target": "{{reasoning.output.target}}"
        }
    ]
)

# Avviare
run = cw.workflows.start(workflow_id=workflow.id)

# Fermare
cw.workflows.stop(workflow_id=workflow.id)

# Listare run
runs = cw.workflows.list_runs(workflow_id=workflow.id)
```

---

### 🕹️ Teleoperation (Real-time WebSocket)

```python
teleop = cw.teleoperation.start(twin_id=twin.id)

teleop.send_command({
    "type": "velocity",
    "linear": {"x": 0.5, "y": 0.0, "z": 0.0},
    "angular": {"z": 0.1}
})

state = teleop.get_state()
```

---

### 🔌 Edge SDK (Hardware Fisico)

Per lavorare direttamente su dispositivi edge (Jetson, RPi):
```bash
pip install cyberwave-edge-python
```

**Bridges disponibili**:
- **ROS2 Bridge** — Interfaccia con topic/servizi ROS2
- **MavLink Bridge** — Interfaccia con flight controller (Pixhawk)

---

## 🌐 REST API

### Base URL
```
https://api.cyberwave.com/api/v1/
```

### Auth Header
```
Authorization: Bearer <CYBERWAVE_API_KEY>
```

### Endpoints

| Risorsa | GET (list) | POST (create) | GET (single) | PUT (update) | DELETE |
|---------|-----------|---------------|-------------|-------------|--------|
| `/assets` | ✅ | ✅ | ✅ | — | — |
| `/twins` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/workspaces` | ✅ | ✅ | — | — | — |
| `/projects` | ✅ | ✅ | — | — | — |
| `/environments` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/simulations` | ✅ | ✅ | ✅ | — | — |
| `/workflows` | ✅ | ✅ | ✅ | — | ✅ |
| `/models` | ✅ | — | ✅ | — | — |

### Endpoints Simulazione (Critici)
| Method | Endpoint | Cosa fa |
|--------|----------|---------|
| `GET` | `/simulations/{id}/status` | Stato simulazione |
| `GET` | `/simulations/{id}/actions` | Azioni disponibili |
| `POST` | `/simulations/{id}/actions` | **Invia comandi al robot** |
| `GET` | `/simulations/{id}/observations` | **Leggi sensori/camera** |
| `POST` | `/simulations/{id}/stop` | Ferma simulazione |

### Endpoints Workflow
| Method | Endpoint | Cosa fa |
|--------|----------|---------|
| `POST` | `/workflows/{id}/start` | Avvia workflow |
| `POST` | `/workflows/{id}/stop` | Ferma workflow |
| `GET` | `/workflows/{id}/runs` | Lista esecuzioni |

---

## 📡 MQTT API (Real-Time)

### Struttura Topic
Pattern: `cyberwave/{resource_type}/{resource_uuid}/{action}`

### Topic Principali

| Topic | Direzione | Cosa fa |
|-------|-----------|---------|
| `cyberwave/joint/{twin_uuid}/update` | Edge→Cloud | Stato joint (singolo, flat multi, aggregato) |
| `cyberwave/twin/{twin_uuid}/position` | Bidirezionale | Posizione del twin |
| `cyberwave/twin/{twin_uuid}/rotation` | Bidirezionale | Rotazione del twin |
| `cyberwave/twin/{twin_uuid}/scale` | Bidirezionale | Scala del twin |
| `cyberwave/twin/{twin_uuid}/telemetry` | Edge→Cloud | Lifecycle events |
| `cyberwave/twin/{twin_uuid}/depth` | Edge→Cloud | Dati depth camera |
| `cyberwave/twin/{twin_uuid}/pointcloud` | Edge→Cloud | Point cloud 3D |
| `cyberwave/twin/{twin_uuid}/metrics` | Edge→Cloud | Metriche performance |
| `cyberwave/twin/{twin_uuid}/navigate/command` | Cloud→Edge | Comandi navigazione |
| `cyberwave/twin/{twin_uuid}/command` | Cloud→Edge | Comandi generici |

### WebRTC Signaling (Video P2P)
| Topic | Cosa fa |
|-------|---------|
| `cyberwave/twin/{twin_uuid}/webrtc-offer` | Offerta connessione P2P |
| `cyberwave/twin/{twin_uuid}/webrtc-answer` | Risposta connessione P2P |
| `cyberwave/twin/{twin_uuid}/webrtc-candidate` | ICE candidate |

### Serializzazione
- **MQTT**: Protobuf (file `.proto` forniti dalla piattaforma)
- **REST**: JSON

---

## 🤖 MCP Server (Model Context Protocol)

### Cos'è
Cyberwave ha un **MCP Server** che permette a un AI assistant (Claude, Cursor, Gemini) di interagire direttamente con i robot.

### Configurazione (Cursor)
```json
{
  "mcpServers": {
    "cyberwave": {
      "command": "npx",
      "args": ["-y", "@cyberwave/mcp-server"],
      "env": {
        "CYBERWAVE_API_KEY": "your-key"
      }
    }
  }
}
```

**Dove mettere il file**:
- **Cursor (Globale)**: `~/.cursor/mcp.json`
- **Cursor (Progetto)**: `.cursor/mcp.json`
- **Claude Desktop**: Dentro la config directory dell'app

### Come funziona
1. L'AI client fa una `tools/list` handshake automatica
2. Scopre tutti i tool disponibili (joint control, locomotion, sensor data, ecc.)
3. L'LLM può direttamente controllare il robot

### Capabilities esposte
- Controllo hardware (set joints, locomozione)
- Gestione environment/simulazione
- Retrieval dati sensori
- Ispezione/controllo digital twin
- Automazione workflow

---

## 🧩 AI Features

### Vision-Language Models (VLM)
- VLM bridgano reasoning AI e azione robotica
- Supporto deploy su cloud o edge
- Pipeline: camera → VLM → azione

### Natural Language → Robot Action
- Comandi in linguaggio naturale → azioni robot
- LLM interpreta e mappa alle API

### Multi-modal Robot Reasoning
- Combina feed camera + sensori + modelli linguistici
- Il robot "capisce" il suo ambiente

### Fleet Behavior Orchestration
- Coordina più robot
- Definisce comportamenti e priorità

### Vision Foundation Models
- Object detection, scene understanding, pose estimation
- Pre-trained, pronti all'uso

---

## ⚡ PATTERN COMPLETO — VLM Pipeline (Esempio Reale)

```python
from cyberwave import Cyberwave
import cv2

# 1. Inizializza
cw = Cyberwave(api_key="your_api_key_here")

# 2. Accedi al digital twin (o robot fisico)
robot = cw.twin("the-robot-studio/so101")

# 3. Cattura dati visivi
frame = robot.capture_frame()  # numpy array

# 4. VLM Inference (integra la tua libreria preferita)
# Esempio con OpenAI GPT-4V, Google Gemini, o locale
# response = vlm_model.generate(frame, prompt="What objects do you see?")
# action = parse_vlm_output(response)

# 5. Controlla il robot
robot.set_joints({"joint_1": 0.5, "joint_2": -0.2})
robot.move_forward(distance=0.5)
```

### Pattern Agente AI Completo
```python
from cyberwave import Cyberwave

cw = Cyberwave()
cw.affect("simulation")  # o "live" per hardware reale

# Setup
robot = cw.twin("unitree/go2")

# Loop agente
while True:
    # 1. PERCEZIONE
    frame = robot.capture_frame()
    
    # 2. RAGIONAMENTO (LLM/VLM)
    decision = my_ai_model.analyze(frame, context="Navigate to the red object")
    
    # 3. AZIONE
    if decision.action == "move_forward":
        robot.move_forward(distance=decision.distance)
    elif decision.action == "turn":
        robot.turn_left() if decision.direction == "left" else robot.turn_right()
    elif decision.action == "done":
        break
```

---

## ⚠️ GOTCHAS & NOTE CRITICHE

1. **API Key obbligatoria** — Prendere dalla Dashboard PRIMA dell'hackathon
2. **Cloud-based** — Serve connessione internet per Digital Twin
3. **Slug format** — I robot si referenziano come `"vendor/model"` (es. `"unitree/go2"`)
4. **`cw.affect("simulation")` vs `cw.affect("live")`** — QUESTO è lo switch sim↔real
5. **MCP è la killer feature** — Configura `@cyberwave/mcp-server` per controllo via LLM
6. **Protobuf per MQTT** — I messaggi real-time usano Protobuf, non JSON
7. **Edge SDK separato** — `cyberwave-edge-python` per hardware on-device
8. **I robot all'hackathon sono FISICI** — Stazioni con onboarding guidato
9. **Docs sono Mintlify** — Usare `/llms.txt` per navigare in modo programmatico
10. **Extras pip** — Installare `cyberwave[camera]` per streaming, `cyberwave[ml]` per ML

---

## 📚 PAGINE DOC — ORDINE DI PRIORITÀ

1. `/llms.txt` — Indice completo (LLM-friendly)
2. `/overview` — Setup iniziale
3. `/sdks/python-sdk` — SDK principale
4. `/sdks/python-sdk-reference` — Referenza classi/metodi
5. `/sdks/mcp` — Integrazione MCP
6. `/feature-reference/architecture/architecture` — Architettura completa
7. `/overview/robotic-arms` — Se lavori con SO101
8. `/overview/robotic-dogs` — Se lavori con Go2
9. `/overview/drones` — Se lavori con droni
10. `/overview/features/workflows` — Per agenti AI
11. `/feature-reference/ai/overview` — Feature AI
12. `/feature-reference/ai/natural-language-to-robot-action` — NLP → Azione
13. `/api-reference/overview` — REST + MQTT API completa
14. `/overview/hardware/unitree-go2` — Specifiche Go2
15. `/overview/hardware/so-arm100` — Specifiche SO101

---

## 🔧 GitHub Repositories

| Repo | URL | Cosa è |
|------|-----|--------|
| Python SDK | `github.com/cyberwave-os/cyberwave-python` | SDK principale |
| Edge Python | `github.com/cyberwave-os/cyberwave-edge-python` | SDK per edge devices |
| Edge Core | `github.com/cyberwave-os/cyberwave-edge-core` | Orchestrazione locale |
| CLI | `github.com/cyberwave-os/cyberwave-cli` | Tool command-line |
| Catalog | `github.com/cyberwave-os/Catalog` | URDF e definizioni robot |
| Docs | `github.com/cyberwave-os/docs-mintlify` | Sorgente docs |

---

## 📊 Protocol Abstraction Table (SDK)

| Feature | Protocollo Sottostante | Metodo SDK |
|---------|----------------------|------------|
| Twin Management | REST | `cw.twin("slug")` |
| Joint Control | MQTT | `twin.joints.set(...)` |
| Asset Catalog | REST | `cw.assets.search(...)` |
| Video Streaming | WebRTC/MQTT | `twin.start_streaming()` |
| Frame Capture | WebRTC | `twin.capture_frame()` |
| Environment | REST | `cw.environments` |
| Mode Switch | REST | `cw.affect("simulation")` |
| Edge Devices | REST + MQTT | `cw.edges` |
| ML Models | REST | `cw.models` |
