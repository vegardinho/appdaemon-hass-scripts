"""Notification service wrapper for sending notifications via Home Assistant."""

def send_email(hass_instance, title, message, target="landsverk.vegard@gmail.com"):
    """
    Send an email notification via Home Assistant smtp_gmail service.
    
    Args:
        hass_instance: The AppDaemon Hass instance to use for service calls
        title: Email subject line
        message: Email body content
        target: Email recipient address
    """
    try:
        hass_instance.call_service(
            "notify/smtp_gmail",
            service_data={
                "title": title,
                "message": message,
                "target": target
            }
        )
        hass_instance.log(f"Email notification sent: {title}")
    except Exception as e:
        hass_instance.log(f"Failed to send email notification: {e}", level="ERROR")

def send_mobile_notification(hass_instance, title, message, device="vegards_telefonino_v2"):
    """
    Send a mobile app notification via Home Assistant.
    
    Args:
        hass_instance: The AppDaemon Hass instance to use for service calls
        title: Notification title
        message: Notification message
        device: Device identifier (without 'mobile_app_' prefix)
    """
    try:
        hass_instance.call_service(
            f"notify/mobile_app_{device}",
            service_data={
                "title": title,
                "message": message
            }
        )
        hass_instance.log(f"Mobile notification sent: {title}")
    except Exception as e:
        hass_instance.log(f"Failed to send mobile notification: {e}", level="ERROR")
