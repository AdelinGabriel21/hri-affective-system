import qi, time

s = qi.Session()
s.connect("tcp://127.0.0.1:9559")
print("conectat:", s.isConnected())

m = s.service("ALMotion")
print("robot treaz:", m.robotIsWakeUp())
time.sleep(1)

m.wakeUp()
print("ok, wakeUp a mers") 