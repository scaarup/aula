"""
Mail (messaging) management for Aula integration.
Handles fetching, parsing, and organizing mail threads.
"""

import logging
import re

_LOGGER = logging.getLogger(__name__)


class MailMixin:
    """Mixin providing mail-related methods for the Aula Client."""

    def _get_mail(self):
        """Fetch mail threads from Aula messaging system"""
        try:
            _LOGGER.info("MAIL: Starting to fetch mail threads...")

            # Debug current session state (do not log cookie values)
            has_session = self._session is not None
            _LOGGER.debug("MAIL: Session active: %s", has_session)

            # Initialize mail data
            self.mail_threads = {}
            self.mail_by_child = {}
            self.mail_child_profile_mapping = {}

            # Fetch multiple pages of mail threads (50 threads total)
            all_threads = []
            for page in range(5):  # Fetch 5 pages of 10 threads each = 50 threads
                try:
                    mail_url = self._make_api_url("messaging.getThreads", sortOn="date", orderDirection="desc", page=page)
                    _LOGGER.debug(f"MAIL: Fetching page {page + 1}")

                    response = self._session.get(
                        mail_url, headers=self._auth_headers(), verify=True, timeout=15
                    )
                    _LOGGER.debug(
                        f"MAIL: Page {page + 1} response status: {response.status_code}"
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if result.get("status", {}).get("message") == "OK":
                            threads_data = result.get("data", {}).get("threads", [])
                            if threads_data:
                                all_threads.extend(threads_data)
                                _LOGGER.info(
                                    f"MAIL: Retrieved {len(threads_data)} threads from page {page + 1}"
                                )

                                # Stop if no more messages exist
                                if not result.get("data", {}).get(
                                    "moreMessagesExist", False
                                ):
                                    _LOGGER.info(
                                        f"MAIL: No more threads available after page {page + 1}"
                                    )
                                    break
                            else:
                                _LOGGER.info(
                                    f"MAIL: No threads found on page {page + 1}, stopping pagination"
                                )
                                break
                        else:
                            _LOGGER.warning(
                                f"MAIL: API returned non-OK status on page {page}: {result.get('status', {})}"
                            )
                            break
                    else:
                        _LOGGER.warning(
                            f"MAIL: API request failed on page {page} with status {response.status_code}"
                        )
                        break

                except Exception as page_error:
                    _LOGGER.error(f"MAIL: Error fetching page {page}: {type(page_error).__name__}")
                    break

            _LOGGER.info(
                f"MAIL: Retrieved total of {len(all_threads)} mail threads across all pages"
            )

            if all_threads:
                self._parse_mail_data(all_threads)
            _LOGGER.info(
                f"MAIL: Successfully processed {len(self.mail_threads)} mail threads"
            )
            _LOGGER.info(
                f"MAIL: Mail distribution by child: {[(child_id, len(threads)) for child_id, threads in self.mail_by_child.items()]}"
            )

            # Debug child ID mapping
            _LOGGER.debug(
                f"MAIL: Available child IDs from _children: {[str(child['id']) for child in getattr(self, '_children', [])]}"
            )
            _LOGGER.debug(
                f"MAIL: Mail threads stored for child IDs: {list(self.mail_by_child.keys())}"
            )

        except Exception as e:
            _LOGGER.error(f"MAIL: Error in _get_mail: {type(e).__name__}")
            self.mail_threads = {}
            self.mail_by_child = {}

    def _parse_mail_data(self, threads_data):
        """Parse mail threads data from getThreads API response"""
        try:
            _LOGGER.info("MAIL: Starting to parse mail threads data")

            self.mail_threads = {}
            self.mail_by_child = {}
            # Create mapping between child IDs and regardingChildren profileIds
            self._create_mail_child_mapping(threads_data)

            for thread in threads_data:
                thread_id = str(thread.get("id", ""))
                if not thread_id:
                    continue

                # Extract thread information
                parsed_thread = {
                    "id": thread_id,
                    "subject": thread.get("subject", ""),
                    "read": thread.get("read", False),
                    "muted": thread.get("muted", False),
                    "marked": thread.get("marked", False),
                    "sensitive": thread.get("sensitive", False),
                    "started_time": thread.get("startedTime", ""),
                    "latest_message": self._extract_latest_message(
                        thread.get("latestMessage", {})
                    ),
                    "creator": self._extract_thread_creator(thread.get("creator", {})),
                    "regarding_children": self._extract_regarding_children(
                        thread.get("regardingChildren", [])
                    ),
                    "recipients_count": len(thread.get("recipients", []))
                    + (thread.get("extraRecipientsCount") or 0),
                    "institution_code": thread.get("institutionCode", ""),
                }

                # Store thread in main mail dictionary
                self.mail_threads[thread_id] = parsed_thread

                # Group threads by related child profiles using sensor child IDs only
                regarding_children = thread.get("regardingChildren", [])
                regarding_child_ids = []
                for child in regarding_children:
                    profile_id = str(child.get("profileId", ""))
                    if profile_id:
                        regarding_child_ids.append(profile_id)

                        # Map by sensor child ID only (no duplication)
                        sensor_child_id = self._get_sensor_child_id_for_profile(
                            profile_id
                        )
                        if sensor_child_id:
                            if sensor_child_id not in self.mail_by_child:
                                self.mail_by_child[sensor_child_id] = []
                            self.mail_by_child[sensor_child_id].append(parsed_thread)

            _LOGGER.info(
                f"MAIL: Successfully parsed {len(self.mail_threads)} mail threads for {len(self.mail_by_child)} children"
            )
        except Exception as e:
            _LOGGER.error(f"MAIL: Error parsing mail data: {type(e).__name__}")
            self.mail_threads = {}
            self.mail_by_child = {}

    def _create_mail_child_mapping(self, threads_data):
        """Create mapping between sensor child IDs and regardingChildren profile IDs"""
        try:
            # Extract profile IDs and display names from mail threads
            profile_name_map = {}
            for thread in threads_data:
                regarding_children = thread.get("regardingChildren", [])
                for child in regarding_children:
                    profile_id = str(child.get("profileId", ""))
                    display_name = child.get("displayName", "")
                    if profile_id and display_name:
                        profile_name_map[profile_id] = display_name

            # Match with existing child names to create mapping
            self.mail_child_profile_mapping = {}
            for child_id, child_name in self._childnames.items():
                child_id_str = str(child_id)
                # Find matching profile by name
                for profile_id, display_name in profile_name_map.items():
                    if child_name.strip() == display_name.strip():
                        self.mail_child_profile_mapping[child_id_str] = profile_id
                        _LOGGER.debug(
                            f"MAIL: Mapped sensor child ID {child_id_str} to profile ID {profile_id}"
                        )
                        break

            _LOGGER.info(
                f"MAIL: Created child profile mapping: {self.mail_child_profile_mapping}"
            )

        except Exception as e:
            _LOGGER.error(f"MAIL: Error creating child mapping: {type(e).__name__}")
            self.mail_child_profile_mapping = {}

    def _get_sensor_child_id_for_profile(self, profile_id):
        """Get sensor child ID for a regardingChildren profile ID"""
        for (
            sensor_child_id,
            mapped_profile_id,
        ) in self.mail_child_profile_mapping.items():
            if mapped_profile_id == profile_id:
                return sensor_child_id
        return None

    def _extract_latest_message(self, latest_message):
        """Extract latest message information from a thread"""
        if not latest_message:
            return {}

        # Clean HTML content for text preview
        html_content = latest_message.get("text", {}).get("html", "")
        clean_text = re.sub(r"<[^>]+>", "", html_content)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        return {
            "id": latest_message.get("id", ""),
            "send_date_time": latest_message.get("sendDateTime", ""),
            "text_html": html_content,
            "text_clean": clean_text,
        }

    def _extract_thread_creator(self, creator):
        """Extract thread creator information"""
        if not creator:
            return {}

        return {
            "full_name": creator.get("fullName", ""),
            "metadata": creator.get("metadata", ""),
            "answer_directly_name": creator.get("answerDirectlyName", ""),
        }

    def _extract_regarding_children(self, regarding_children):
        """Extract information about children the thread regards"""
        extracted_children = []
        for child in regarding_children:
            if child:
                extracted_children.append(
                    {
                        "profile_id": str(child.get("profileId", "")),
                        "display_name": child.get("displayName", ""),
                        "short_name": child.get("shortName", ""),
                    }
                )
        return extracted_children
