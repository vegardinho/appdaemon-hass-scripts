import hassapi as hass
import requests
from datetime import datetime, timedelta
import requests
import json
import os

class HelloWorld(hass.Hass):
    def initialize(self):
        self.log("Hello from AppDaemon")
        self.log("You are now ready to run Apps!")

class TurnOnLight(hass.Hass):
    def initialize(self):
        # Schedule the light to turn on at 23:00 every night
        self.log("TurnOnLight initialized")
        self.run_daily(self.turn_off_light, "21:20:00")

    def turn_off_light(self, kwargs):
        self.turn_off("light.vegards_soverom")
        self.log("Turned off light.vegards_soverom")