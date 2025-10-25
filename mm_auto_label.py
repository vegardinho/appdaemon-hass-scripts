import hassapi as hass
from datetime import datetime
from mm_api import MattermostCustom
import arrow


class MmAutoLabel(hass.Hass):
    """
    Update Mattermost status and iOS focus based on calendar events and Home Assistant location.
    """

    def initialize(self):
        """
        Setup Mattermost API object and configure listeners for state changes.
        """
        self.user_id = self.args.get("mm_user_id")
        self.token = self.args.get("mm_token")
        self.mattermost = MattermostCustom(self.user_id, self.token)

        # Listen for state changes in person and calendar entities
        self.listen_state(
            self.calculate_status, "person.vegard_landsverk", immediate=True
        )
        self.listen_state(
            self.calculate_status,
            "calendar.calendario",
            attribute="all",
            immediate=True,
        )
        self.listen_state(
            self.calculate_status, "calendar.it_hjelp", attribute="all", immediate=True
        )

    def calculate_status(self, entity, attribute, old, new, **kwargs):
        """
        Calculate Mattermost status based on calendar events and Home Assistant location.
        """
        self.log(f'\nTriggered by "{entity}" with new state "{new}"')

        # Get current zone of the person
        zone = self.get_state("person.vegard_landsverk")
        self.log(f"Current zone: {zone}")

        # Get current iOS focus
        current_focus = self.get_state("input_text.ios_focus")

        # Get details of the uio event
        uio_event = self.get_state("calendar.calendario", attribute="all")
        active_uio_event = uio_event.get("state") == "on"
        uio_event_location = uio_event.get("attributes", {}).get("location", "")
        uio_event_title = uio_event.get("attributes", {}).get("message", "")
        self.log(
            f'UiO event ({"active" if active_uio_event else "not active"}): "{uio_event_title}": "{uio_event}"'
        )

        # Get details of the job event
        job_event = self.get_state("calendar.it_hjelp", attribute="all")
        active_job_event = job_event.get("state") == "on" if job_event else False
        self.log(
            f'Job event ({"active" if active_job_event else "not active"}): "{job_event}"'
        )

        # Set fallback values
        status_text = "Svever rundt i eteren"
        emoji = "flying_saucer"
        focus = "Personal"
        work = False
        expires = None

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

        # Helper to safely convert various time representations to an Arrow object
        def _to_arrow(value):
            """Return an arrow.Arrow for value, or None if it cannot be parsed."""
            if not value:
                return None
            # If it's already a datetime
            if isinstance(value, datetime):
                try:
                    return arrow.get(value).to("Europe/Oslo")
                except Exception:
                    return None
            # If it's a string, try isoformat first, then fallback to arrow parsing
            if isinstance(value, str):
                try:
                    return arrow.get(datetime.fromisoformat(value), tzinfo="Europe/Oslo")
                except Exception:
                    try:
                        return arrow.get(value).to("Europe/Oslo")
                    except Exception:
                        return None
            # Fallback: let arrow try to handle it
            try:
                return arrow.get(value).to("Europe/Oslo")
            except Exception:
                return None

        # Make sure there is an expiration time, then convert to iso format
        if not expires:
            job_start = job_event.get("attributes", {}).get("start_time") if job_event else None
            uio_start = uio_event.get("attributes", {}).get("start_time") if uio_event else None
            job_expire = _to_arrow(job_start)
            uio_expire = _to_arrow(uio_start)

            # Choose the later of the two start times if available
            if job_expire and uio_expire:
                expires_arrow = job_expire if job_expire > uio_expire else uio_expire
            else:
                expires_arrow = job_expire or uio_expire
        else:
            expires_arrow = _to_arrow(expires)

        expires = expires_arrow.isoformat() if expires_arrow else None


        # Append location to status if job or uio event
        if work:
            if zone == "home":
                status_text += " (Hjemmekontor)"
            elif zone != "not_home":
                status_text += f" ({zone})"

        self.log(
            f'New status: "{status_text}" with emoji "{emoji}" and expires "{expires}"'
        )

        # Set new Mattermost status and iOS focus
        try:
            self.mattermost.set_status(emoji=emoji, text=status_text, expires=expires)
        except ValueError as e:
            # Token expired or invalid
            self.log(f"ERROR: {e}", level="ERROR")
        except Exception as e:
            # Other errors
            self.log(f"ERROR: Failed to update Mattermost status: {e}", level="ERROR")

        # if current_focus != focus:
        #     self.set_focus(focus)

    def set_focus(self, focus):
        """
        Sets iOS focus by sending custom mail that triggers focus automation on iPhone.
        """
        title = "[APPDAEMON] Set iOS focus"
        message = f"Trigger automation to set focus to {focus}\n\nSent by Home Assistant SMTP integration."
        self.log(f"Sending email to change focus")
        self.call_service(
            "notify/smtp_gmail",
            title=title,
            message=message,
            target="vegard.landsverk@icloud.com",
        )
