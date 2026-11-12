# Emotion Robotic Lamp - 4 DOF

## Project Status

ROS 2 Jazzy robotic lamp with:
- CAD model and 4 revolute joints
- CPG Personality: Happy, Sad, Curious, Neutral
- Motion Controller
- RViz2 visualization
- Perception package for future development

Tested on Docker linux/amd64 using a Mac M2.
Not yet tested directly on Windows.

## Windows Requirements

- Windows 10/11 (Intel or AMD CPU)
- Docker Desktop with WSL 2
- VNC Viewer

## Start Project

1. Extract the handover folder.
2. Start Docker Desktop.
3. Double-click START_LAMP.bat.
4. Set a VNC password on first startup.
5. Wait until RViz2 starts.
6. Connect VNC Viewer to localhost:5901.

## ROS 2 Workspace

Workspace location inside Docker:
/workspace

Source code location:
/workspace/src

Main packages:
- lamp_cad_4dof
- lamp_cpg
- lamp_control
- lamp_description
- lamp_perception

## Develop Further

Open a terminal in the handover folder:

docker compose exec emotion_lamp bash

Then run:

source /opt/ros/jazzy/setup.bash
source /workspace/install/setup.bash

After changing ROS 2 source code, rebuild:

cd /workspace
colcon build --symlink-install
source /workspace/install/setup.bash

Restart the relevant ROS nodes to load updated code.

## CPG Personality Commands

Enable interaction:

ros2 topic pub --times 5 --rate 2 /lamp/interaction_enabled std_msgs/msg/Bool "{data: true}"

Happy:
ros2 topic pub --once /lamp/personality_mode std_msgs/msg/String "{data: happy}"

Sad:
ros2 topic pub --once /lamp/personality_mode std_msgs/msg/String "{data: sad}"

Curious:
ros2 topic pub --once /lamp/personality_mode std_msgs/msg/String "{data: curious}"

Neutral:
ros2 topic pub --once /lamp/personality_mode std_msgs/msg/String "{data: neutral}"

## Important Notes

- Do not start view_cad.launch.py together with view_with_cpg.launch.py.
- Avoid duplicate publishers on /joint_states.
- Joint pivots and directions should be verified before hardware control.
- The original Mac camera bridge is not included.
- USB camera integration on Windows is future work.
- The source files are editable.
- Docker environment must be tested on the recipient's Windows machine.
