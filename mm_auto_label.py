import hassapi as hass
import requests
from datetime import datetime, timedelta
import json
import os
from mattermost_custom import MattermostCustom
import pytz
import arrow
from dateutil import tz

class MMAutoLabel(hass.Hass):
    """
    Update Mattermost status and iOS focus based on calendar events and Home Assistant location.
    """

    def initialize(self):
        """
        Setup Mattermost API object and configure listeners for state changes.
        """
        self.user_id = self.args.get('mm_user_id')
        self.token = self.args.get('mm_token')
        self.mattermost = MattermostCustom(self.user_id, self.token)

        # Listen for state changes in person and calendar entities
        self.listen_state(self.calculate_status, "person.vegard_landsverk")
        self.listen_state(self.calculate_status, "calendar.calendario")
        self.listen_state(self.calculate_status, "calendar.jobb")

    def calculate_status(self, entity, attribute, old, new, kwargs):
        """
        Calculate Mattermost status based on calendar events and Home Assistant location.
        """
        self.log(f"Triggered by \"{entity}\" with new state \"{new}\"")
        
        # Get current zone of the person
        zone = self.get_state("person.vegard_landsverk")

        # Get current iOS focus
        current_focus = self.get_state("input_text.ios_focus")

        # Get details of the uio event
        uio_event = self.get_state("calendar.calendario", attribute="all")
        active_uio_event = uio_event.get("state") == "on"
        uio_event_location = uio_event.get("attributes", {}).get("location", "")
        uio_event_title = uio_event.get("attributes", {}).get("message", "")

        # Get details of the job event
        job_event = self.get_state("calendar.jobb", attribute="all")
        active_job_event = job_event.get("state") == "on" if job_event else False

        # Set fallback values
        status_text = "Svever rundt i eteren"
        emoji = "flying_saucer"
        focus = "Personal"
        work = False
        expires = job_event.get("attributes", {}).get("start_time")

        # Update status if there is an active job event
        if active_job_event:
            status_text = "På jobb"
            emoji = "briefcase"
            focus = "Trabajo"
            work = True
            expires = job_event.get("attributes", {}).get("end_time")

        # Update status if there is an active university event
        if active_uio_event:
            # Set to studying only if present at lecture
            if "PSYC" in uio_event_title:
                # Separate if-check to not register absent lecture as meeting
                if zone in uio_event_location:
                    status_text = "På forelesning"
                    emoji = "books"
                    focus = "Estudiar"
                    work = False
                    expires = uio_event.get("attributes", {}).get("end_time")
            else:
                status_text = "I møte"
                emoji = "speech_balloon"
                expires = uio_event.get("attributes", {}).get("end_time")

        # Convert to ISO format
        expires = arrow.get(datetime.fromisoformat(expires), tzinfo="Europe/Oslo").isoformat()

        # Append location to status if job or uio event
        if work:
            if zone == "home":
                status_text += " (Hjemmekontor)"
            elif zone != "not_home":
                status_text += f" ({zone})"

        self.log(f"New status: \"{status_text}\" with emoji \"{emoji}\" and expires \"{expires}\"")

        # Set new Mattermost status and iOS focus
        self.mattermost.set_status(emoji=emoji, text=status_text, expires=expires)

        # if current_focus != focus:
        #     self.set_focus(focus)

    def set_focus(self, focus):
        """
        Sets iOS focus by sending custom mail that triggers focus automation on iPhone.
        """
        title = "[APPDAEMON] Set iOS focus"
        message = f"Trigger automation to set focus to {focus}\n\nSent by Home Assistant SMTP integration."
        self.log(f"Sending email to change focus")
        self.call_service("notify/smtp_gmail", title=title, message=message, target="vegard.landsverk@icloud.com")

