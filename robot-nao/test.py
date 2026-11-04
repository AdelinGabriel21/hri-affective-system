import os
import time

import qi
from dotenv import load_dotenv

load_dotenv()

# Acelasi robot ca bridge-ul: NAO_IP / NAO_PORT din .env (run.sh completeaza portul robotului virtual)
url = f"tcp://{os.getenv('NAO_IP') or '127.0.0.1'}:{os.getenv('NAO_PORT') or '9559'}"
print("conectare la", url)

s = qi.Session()
s.connect(url)
print("conectat:", s.isConnected())

m = s.service("ALMotion")
print("robot treaz:", m.robotIsWakeUp())
time.sleep(1)

m.wakeUp()
print("ok, wakeUp a mers")
