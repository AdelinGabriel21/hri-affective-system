# Connecting to a Physical or Virtual NAO Robot with Python and `qi`

This guide explains how to set up your environment using Windows Subsystem for Linux (WSL), configure network access for either a physical or virtual NAO robot (in Choregraphe), and run Python control scripts via the `qi` framework.

---

## Prerequisites and Required Tools

Before starting, ensure the following software is installed and configured on your host machine:

* **Choregraphe Suite** (for virtual simulation and visual programming)
* **Robot Settings App** (for physical robot network configuration)
* **WSL 2** (Ubuntu or Debian recommended) with Python installed inside the distribution
* **Visual Studio Code** with the [WSL Extension](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-wsl) installed

---

## Step 1: Connect to WSL inside VS Code

1. Open Visual Studio Code on Windows.
2. Open the Command Palette by pressing `Ctrl + Shift + P`.
3. Type and select:
   `​`​`text
   WSL: Connect to WSL
   `​`​`
4. VS Code will reopen in the context of your default WSL distribution.

---

## Step 2: Create a Project Directory

Inside the VS Code integrated terminal (or your WSL terminal), create and enter a new directory for your project:

`​`​`bash
mkdir nao_project
cd nao_project
code .
`​`​`

---

## Step 3: Set Up a Python Virtual Environment

Isolate your project dependencies by creating and activating a virtual environment:

`​`​`bash
# Create the virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate
`​`​`

> **Note:** Ensure your virtual environment remains active whenever you run scripts or install packages.

---

## Step 4: Install the `qi` Library

Install the Python binding for Aldebaran's NAOqi framework:

`​`​`bash
pip install qi
`​`​`

---

## Step 5: Project Structure and Configuration

A standard minimal project structure looks like this:

`​`​`text
nao_project/
├── .env
├── main.py
└── venv/
`​`​`

Create a `.env` file in the root of your project directory to store your connection parameters:

`​`​`ini
NAO_IP="127.0.0.1"
NAO_PORT="9559"
`​`​`

* Ensure all values are wrapped in quotes.
* Keep connection variables separate from code logic so you can easily switch between physical and virtual targets.

---

## Step 6: Configure the Robot Connection

### Option A: Connecting to a Physical Robot

1. Connect both your computer and the physical NAO robot to the same local Wi-Fi or Ethernet network.
2. Retrieve the robot's IP address:
   * Press the chest button once (NAO will speak its assigned IP address).
   * Alternatively, look up the IP inside the **Robot Settings** desktop app.
3. Update your `.env` file:
   `​`​`ini
   NAO_IP="<ROBOT_IP_ADDRESS>"
   NAO_PORT="9559"
   `​`​`

---

### Option B: Connecting to a Virtual Robot (Choregraphe + WSL Mirrored Mode)

When you open Choregraphe without connecting to a physical robot, it runs a virtual NAO instance bound to local port `9559`. 

Because your code runs inside WSL while Choregraphe runs on Windows, standard WSL network isolation will block direct communication to `127.0.0.1`. You must configure WSL to use **mirrored networking mode**, which shares the `localhost` network namespace between Windows and Linux.

#### 1. Configure `.wslconfig`
1. Press `Win + R`, type `%USERPROFILE%`, and press **Enter**.
2. Locate or create a file named `.wslconfig` in this directory (`C:\Users\<YourUsername>\.wslconfig`).
3. Add the following configuration:

`​`​`ini
[wsl2]
networkingMode=mirrored
`​`​`

4. Save and close the file.

#### 2. Restart WSL
Open PowerShell as Administrator and restart the WSL subsystem:

`​`​`powershell
wsl --shutdown
`​`​`

*(Optional: Restart your PC if changes do not take effect immediately.)*

#### 3. Update the `.env` Configuration
Open your project in VS Code (connected to WSL) and set the `.env` values to loopback:

`​`​`ini
NAO_IP="127.0.0.1"
NAO_PORT="9559"
`​`​`

---

## Step 7: Running Test Code

### 1. Export Environment Variables

Before running your Python script, load the `.env` variables into your current shell session:

`​`​`bash
set -a; source .env; set +a
`​`​`

### 2. Test Script (`main.py`)

Create a file named `main.py` with the following verification script:

`​`​`python
import os
import sys
import time
import qi

def main():
    # 0. Load environment variables
    ip = os.getenv("NAO_IP")
    port = int(os.getenv("NAO_PORT", "9559"))

    connection_url = f"tcp://{ip}:{port}"
    print(f"[INFO] Attempting connection to {connection_url}...")

    # 1. Initialize session and connect via TCP
    session = qi.Session()
    try:
        session.connect(connection_url)
        print("[SUCCESS] Connected to the NAO robot!")
    except RuntimeError as e:
        print(f"[ERROR] Connection failed: {e}")
        sys.exit(1)

    # 2. Acquire required hardware proxy services
    motion = session.service("ALMotion")
    posture = session.service("ALRobotPosture")

    # 3. Wake up the robot (engages motor stiffness) and assume default stance
    motion.wakeUp()
    posture.goToPosture("StandInit", 0.5)

    # 4. Command the right arm to raise
    # RShoulderPitch controls up/down (negative moves arm forward/up)
    # RShoulderRoll controls lateral abduction/adduction
    joint_names = ["RShoulderPitch", "RShoulderRoll"]
    target_angles = [-1.5, -0.2]  # Radians: -1.5 rad is approx. 85 degrees up
    fraction_max_speed = 0.2       # Safety limit: fraction of max joint velocity (0.0 to 1.0)

    print("[INFO] Raising right arm...")
    motion.setAngles(joint_names, target_angles, fraction_max_speed)

    # Hold the position for 3 seconds
    time.sleep(3)

    # 5. Return to a safe crouching position and remove motor stiffness
    print("[INFO] Crouching and resting motors...")
    posture.goToPosture("Crouch", 0.5)
    motion.rest()

if __name__ == "__main__":
    main()
`​`​`

Run the script inside your terminal:

`​`​`bash
python main.py
`​`​`

---

## Execution Pipeline Breakdown

Every script interacting with NAO follows this core lifecycle:

| Stage | Step in Code | Description |
| :--- | :--- | :--- |
| **Session Initialization** | Steps 0 & 1 | Establishes a TCP socket connection via `qi.Session()` using the IP and port targets. |
| **Service Acquisition** | Step 2 | Queries the broker for specific service proxies (e.g., `ALMotion`, `ALRobotPosture`, `ALTextToSpeech`). |
| **Execution Block** | Steps 3 & 4 | Enables motor stiffness (`wakeUp()`), verifies safe posture, and executes kinematic/behavioral trajectories. |
| **Teardown & Safety** | Step 5 | Places the robot in a mechanically stable rest position (`Crouch`) and disengages joint stiffness (`rest()`). |

> **Critical Safety Warning:** Never skip `motion.rest()`. If a program crashes or terminates without removing motor stiffness, the joint actuators remain energized under load, leading to rapid overheating and potential permanent hardware failure. Always wrap movement routines in `try...finally` blocks in production to guarantee that `motion.rest()` executes.