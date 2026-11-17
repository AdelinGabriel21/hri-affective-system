import os
import time

import qi
from dotenv import load_dotenv

load_dotenv()

url = f"tcp://{os.getenv('NAO_IP') or '127.0.0.1'}:{os.getenv('NAO_PORT') or '9559'}"
print("conectare la", url)

s = qi.Session()
s.connect(url)
print("conectat:", s.isConnected())

motion = s.service("ALMotion")
posture = s.service("ALRobotPosture")
tts = s.service("ALTextToSpeech")

# 1. rest -> wakeUp, ca sa se vada schimbarea
motion.rest()
print("rest, treaz:", motion.robotIsWakeUp())
time.sleep(1)
motion.wakeUp()
print("wakeUp, treaz:", motion.robotIsWakeUp())

# 2. postura
posture.goToPosture("StandInit", 0.5)
print("postura curenta:", posture.getPosture())

# 3. capul stanga-dreapta
for yaw in (0.6, -0.6, 0.0):
    motion.angleInterpolationWithSpeed("HeadYaw", yaw, 0.2)
print("cap miscat")

# 4. vorbire (pe robotul virtual apare in panoul Dialog din Choregraphe)
tts.say("Salut, sunt conectat")
print("gata")
