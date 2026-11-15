"""Posts and mail sensors for Aula integration."""

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.helpers.entity import Entity

from ..const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class AulaPostsSensor(Entity):
    """Sensor for Aula posts/news (indlæg)"""

    def __init__(self, hass, coordinator, child):
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Posts {childname}"

    @property
    def state(self):
        try:
            child_id = str(self._child["id"])
            posts = self._client.posts_by_child.get(child_id, [])

            _LOGGER.debug(f"POSTS SENSOR: Child {child_id} has {len(posts)} posts")

            # Return total posts count instead of recent posts
            return len(posts)

        except Exception as e:
            _LOGGER.error(f"POSTS SENSOR: Error getting posts sensor state: {e}")
            return "unavailable"

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            child_id = str(self._child["id"])
            posts = self._client.posts_by_child.get(child_id, [])

            _LOGGER.debug(
                f"POSTS SENSOR: Processing {len(posts)} posts for child {child_id}"
            )

            # Get recent posts (last 30 days) with details (limit to 5 most recent)
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=30)
            recent_posts = []
            important_posts = []

            for post in posts[:10]:  # Limit to 5 most recent for cleaner output
                try:
                    # Handle timestamp with proper timezone parsing
                    timestamp_str = post["timestamp"]
                    post_date = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    if post_date.tzinfo is None:
                        post_date = post_date.replace(tzinfo=timezone.utc)

                    post_summary = {
                        "title": post["title"],
                        "content_preview": post["content"]["text"][:100]
                        + ("..." if len(post["content"]["text"]) > 100 else ""),
                        "timestamp": post["timestamp"],
                    }

                    if post_date >= cutoff_date:
                        recent_posts.append(post_summary)

                    if post["is_important"]:
                        important_posts.append(post_summary)

                except (ValueError, TypeError, KeyError) as e:
                    _LOGGER.debug(
                        f"POSTS SENSOR: Error processing post {post.get('id', 'unknown')}: {e}"
                    )
                    continue

            attributes["recent_posts"] = recent_posts
            attributes["important_posts"] = important_posts
            attributes["total_posts"] = len(posts)
            attributes["recent_count"] = len(recent_posts)
            attributes["important_count"] = len(important_posts)

            # Latest post basic info
            if posts:
                latest_post = posts[0]  # Posts should be sorted by timestamp
                attributes["latest_post"] = {
                    "title": latest_post["title"],
                    "timestamp": latest_post["timestamp"],
                }

            _LOGGER.debug(
                f"POSTS SENSOR: Set attributes - total: {len(posts)}, recent: {len(recent_posts)}, important: {len(important_posts)}"
            )

        except Exception as e:
            _LOGGER.error(f"POSTS SENSOR: Error in posts sensor attributes: {e}")
            attributes["error"] = str(e)

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"posts_{childname}"

    @property
    def device_class(self):
        return None

    @property
    def unit_of_measurement(self):
        return "posts"

    @property
    def icon(self):
        return "mdi:post-outline"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )


class AulaMailSensor(Entity):
    """Sensor for Aula mail threads"""

    def __init__(self, hass, coordinator, child):
        self._hass = hass
        self._coordinator = coordinator
        self._child = child
        self._client = hass.data[DOMAIN]["client"]

    @property
    def name(self):
        childname = self._client._childnames[self._child["id"]].split()[0]
        return f"Mail {childname}"

    @property
    def state(self):
        try:
            child_id = str(self._child["id"])
            mail_threads = self._client.mail_by_child.get(child_id, [])

            _LOGGER.debug(
                f"MAIL SENSOR: Child {child_id} has {len(mail_threads)} mail threads"
            )
            _LOGGER.debug(
                f"MAIL SENSOR: Available mail children: {list(self._client.mail_by_child.keys())}"
            )

            # Return total mail threads count
            return len(mail_threads)

        except Exception as e:
            _LOGGER.error(
                f"MAIL SENSOR: Error getting state for child {self._child.get('id', 'unknown')}: {e}"
            )
            return 0

    @property
    def extra_state_attributes(self):
        attributes = {}

        try:
            child_id = str(self._child["id"])
            mail_threads = self._client.mail_by_child.get(child_id, [])

            _LOGGER.debug(
                f"MAIL SENSOR: Processing {len(mail_threads)} mail threads for child {child_id}"
            )

            # Get recent threads (limit to 5 most recent)
            recent_threads = []
            unread_count = 0
            marked_count = 0

            for thread in mail_threads[:10]:  # Limit to 5 most recent for cleaner output
                try:
                    thread_summary = {
                        "subject": thread["subject"],
                        "creator": thread["creator"].get("full_name", "Unknown"),
                        "started_time": thread["started_time"],
                        "read": thread["read"],
                        "marked": thread["marked"],
                        "sensitive": thread["sensitive"],
                        "latest_message_preview": thread["latest_message"].get(
                            "text_clean", ""
                        )[:100]
                        + (
                            "..."
                            if len(thread["latest_message"].get("text_clean", "")) > 100
                            else ""
                        ),
                    }

                    recent_threads.append(thread_summary)

                except (ValueError, TypeError, KeyError) as e:
                    _LOGGER.debug(
                        f"MAIL SENSOR: Error processing thread {thread.get('id', 'unknown')}: {e}"
                    )
                    continue

            # Count unread and marked across all threads
            for thread in mail_threads:
                if not thread.get("read", True):
                    unread_count += 1
                if thread.get("marked", False):
                    marked_count += 1

            # Set attributes
            attributes["recent_threads"] = recent_threads
            attributes["total_threads"] = len(mail_threads)
            attributes["unread_count"] = unread_count
            attributes["marked_count"] = marked_count

            # Latest thread basic info
            if mail_threads:
                latest_thread = mail_threads[0]  # Threads should be sorted by date
                attributes["latest_thread"] = {
                    "subject": latest_thread["subject"],
                    "started_time": latest_thread["started_time"],
                    "creator": latest_thread["creator"].get("full_name", "Unknown"),
                    "read": latest_thread["read"],
                }

            _LOGGER.debug(
                f"MAIL SENSOR: Set attributes - total: {len(mail_threads)}, unread: {unread_count}, marked: {marked_count}"
            )

        except Exception as e:
            _LOGGER.error(f"MAIL SENSOR: Error in mail sensor attributes: {e}")
            attributes["error"] = str(e)

        return attributes

    @property
    def unique_id(self):
        childname = (
            self._client._childnames[self._child["id"]]
            .split()[0]
            .lower()
            .replace(" ", "_")
        )
        return f"mail_{childname}"

    @property
    def device_class(self):
        return None

    @property
    def unit_of_measurement(self):
        return "threads"

    @property
    def icon(self):
        return "mdi:email-outline"

    @property
    def should_poll(self):
        return False

    @property
    def available(self):
        return self._coordinator.last_update_success

    async def async_update(self):
        await self._coordinator.async_request_refresh()

    async def async_added_to_hass(self):
        self.async_on_remove(
            self._coordinator.async_add_listener(self.async_write_ha_state)
        )
