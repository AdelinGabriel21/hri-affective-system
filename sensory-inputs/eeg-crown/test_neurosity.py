import os
import time
from dotenv import load_dotenv
from neurosity import NeurositySDK

load_dotenv()

neurosity = NeurositySDK({"device_id": os.getenv("NEUROSITY_DEVICE_ID")})
neurosity.login({
    "email": os.getenv("NEUROSITY_EMAIL"),
    "password": os.getenv("NEUROSITY_PASSWORD")
})


def show(name):
    def callback(data):
        print(name, data)
    return callback


neurosity.brainwaves_power_by_band(show("BANDS"))
neurosity.calm(show("CALM"))
neurosity.focus(show("FOCUS"))
neurosity.signal_quality(show("QUALITY"))

time.sleep(15)