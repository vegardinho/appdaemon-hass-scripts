import hassapi as hass
import requests
from datetime import datetime, timedelta
import json
import os
from mattermost_custom import MattermostCustom

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
        self.listen_state(self.calculate_status, "calendar.vegard", attribute="message", constrain_state=lambda x: x == "Jobb")
        # self.listen_state(self.calculate_status, "sensor.vegards_telefonino_geocoded_zone", attribute="Zones")

    def calculate_status(self, entity, attribute, old, new, kwargs):
        """
        Calculate Mattermost status based on calendar events and Home Assistant location.
        """
        # Get current zone of the person
        zone = self.get_state("person.vegard_landsverk")

        # Get details of the uio event
        uio_event = self.get_state("calendar.calendario", attribute="all")
        active_uio_event = uio_event.get("state") == "on"
        uio_event_location = uio_event.get("attributes", {}).get("location", "")
        uio_event_title = uio_event.get("attributes", {}).get("message", "")

        # Get details of the job event
        vegard_event = self.get_state("calendar.vegard", attribute="all")
        vegard_job_event = vegard_event if vegard_event.get("attributes", {}).get("message", "") == "Jobb" else None
        active_vegard_job_event = vegard_job_event.get("state") == "on" if vegard_job_event else False

        # Set fallback values
        status_text = "Svever rundt i eteren"
        emoji = "flying_saucer"
        focus = "Personal"

        # Update status if there is an active job event
        if active_vegard_job_event:
            status_text = "På jobb"
            emoji = "briefcase"
            focus = "Trabajo"

        # Update status if there is an active university event
        if active_uio_event:
            # Set to studying only if present at lecture
            if "PSYC" in uio_event_title:
                # Separate if-check to not register absent lecture as meeting
                if (zone == "home") or (zone in uio_event_location):
                    # Check for Jobb-event before and after the uio event on the same day

                    # TODO: un-comment this when appdaemon 4.5 is out (see https://github.com/AppDaemon/appdaemon/issues/1837)
                    # TODO: Test with different overlapping events
                    # uio_event_start = datetime.fromisoformat(uio_event.get("attributes", {}).get("start_time"))
                    # uio_event_end = datetime.fromisoformat(uio_event.get("attributes", {}).get("end_time"))

                    # jobb_events = [event for event in jobb_events if event.get("summary") == "Jobb"]
                    # end_of_today = datetime.now().replace(hour=23, minute=59, second=59).strftime("%Y-%m-%d %H:%M:%S")
                    # job_events = self.call_service("calendar/get_events", entity_id="calendar.vegard", end=end_of_today, return_response=True)
                    # self.log(job_events)

                    # jobb_start_before_start = any(datetime.fromisoformat(event.get("start")) < uio_event_start for event in jobb_events) # Starts before lecture has started
                    # jobb_end_after_start = any(datetime.fromisoformat(event.get("end")) > uio_event_start for event in jobb_events) # Ends after lecuture has started
                    # jobb_start_after_start = any(datetime.fromisoformat(event.get("start")) >= uio_event_end for event in jobb_events) # Starts after lecture has started
                    # jobb_end_before_end = any(datetime.fromisoformat(event.get("end")) < uio_event_end for event in jobb_events) # Ends before lecture has ended

                    # if (jobb_start_before_start and jobb_end_after_start) or (jobb_start_after_start and jobb_end_after_start):
                    status_text = "På forelesning"
                    emoji = "books"
                    focus = "Estudiar"
            else:
                status_text = "I møte"
                emoji = "speech_balloon"

        # Append location to status if job or uio event
        if active_uio_event or active_vegard_job_event:
            if zone == "home":
                status_text += " (Hjemmkontor)"
            elif zone != "not_home":
                status_text += f" ({zone})"

        self.log(f"New status: \"{status_text}\" with emoji \"{emoji}\"")

        # Set new Mattermost status and iOS focus
        self.mattermost.set_status(emoji=emoji, text=status_text, expires=None)
        self.set_focus(focus)

    def set_focus(self, focus):
        """
        Sets iOS focus by sending custom mail that triggers focus automation on iPhone.
        """
        self.log(f"Setting focus to {focus}")
        self.call_service("input_text/set_value", entity_id="input_text.ios_focus", value=focus)
