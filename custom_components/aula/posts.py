"""
Posts (Indlæg) management for Aula integration.
Handles fetching and parsing posts/news from Aula.
"""

import logging
import re

_LOGGER = logging.getLogger(__name__)


class PostsMixin:
    """Mixin providing posts-related methods for the Aula Client."""

    def _get_posts(self):
        """Fetch posts/news (indlæg) from Aula with pagination"""
        try:
            _LOGGER.debug("Starting to fetch posts data...")

            # Initialize posts data
            self.posts = {}
            self.posts_by_child = {}

            all_posts = []

            # Fetch multiple pages to get the last 50 posts
            for index in [
                0,
                10,
                20,
                30,
                40,
            ]:  # Fetch 5 pages of 10 posts each = 50 posts
                try:
                    posts_url = self._make_api_url("posts.getAllPosts", parent="profile", index=index, limit=10, institutionProfileIds=self._institutionProfileIdsList)
                    _LOGGER.debug(
                        f"POSTS: Fetching page {index // 10 + 1}"
                    )

                    response = self._session.get(posts_url, headers=self._auth_headers(), verify=True, timeout=15)
                    _LOGGER.debug(
                        f"POSTS: Page {index // 10 + 1} response status: {response.status_code}"
                    )

                    if response.status_code == 200:
                        result = response.json()
                        if result.get("status", {}).get("message") == "OK":
                            posts_data = result.get("data", {}).get("posts", [])
                            if posts_data:
                                all_posts.extend(posts_data)
                                _LOGGER.info(
                                    f"POSTS: Retrieved {len(posts_data)} posts from page {index // 10 + 1}"
                                )
                            else:
                                _LOGGER.info(
                                    f"POSTS: No more posts at index {index}, stopping pagination"
                                )
                                break
                        else:
                            _LOGGER.warning(
                                f"POSTS: API returned non-OK status at index {index}: {result.get('status', {})}"
                            )
                            break
                    else:
                        _LOGGER.warning(
                            f"POSTS: API request failed at index {index} with status {response.status_code}"
                        )
                        break

                except Exception as page_error:
                    _LOGGER.error(
                        f"POSTS: Error fetching page at index {index}: {type(page_error).__name__}"
                    )
                    break

            _LOGGER.info(
                f"POSTS: Retrieved total of {len(all_posts)} posts across all pages"
            )

            if all_posts:
                self._parse_posts_data(all_posts)
                _LOGGER.info(f"POSTS: Successfully processed {len(self.posts)} posts")
                _LOGGER.info(
                    f"POSTS: Posts distribution by child: {[(child_id, len(posts)) for child_id, posts in self.posts_by_child.items()]}"
                )
            else:
                _LOGGER.warning("POSTS: No posts data found across all pages")

        except Exception as e:
            _LOGGER.error(f"POSTS: Error in _get_posts: {type(e).__name__}")
            self.posts = {}
            self.posts_by_child = {}

    def _parse_posts_data(self, posts_data):
        """Parse posts data from getAllPosts API response"""
        try:
            _LOGGER.info("POSTS: Starting to parse posts data")

            self.posts = {}
            self.posts_by_child = {}

            for post in posts_data:
                post_id = str(post.get("id", ""))
                if not post_id:
                    continue

                # Extract basic post information
                parsed_post = {
                    "id": post_id,
                    "title": post.get("title", ""),
                    "content": self._extract_post_content(post.get("content", {})),
                    "timestamp": post.get("timestamp", ""),
                    "publish_at": post.get("publishAt", ""),
                    "expire_at": post.get("expireAt", ""),
                    "is_important": post.get("isImportant", False),
                    "important_from": post.get("importantFrom"),
                    "important_to": post.get("importantTo"),
                    "owner": self._extract_post_owner(post.get("ownerProfile", {})),
                    "attachments": self._extract_post_attachments(
                        post.get("attachments", [])
                    ),
                    "shared_with_groups": self._extract_shared_groups(
                        post.get("sharedWithGroups", [])
                    ),
                    "related_profiles": self._extract_related_profiles(
                        post.get("relatedProfiles", [])
                    ),
                    "comment_count": post.get("commentCount", 0),
                    "allow_comments": post.get("allowComments", False),
                    "edited_at": post.get("editedAt"),
                }

                # Store post in main posts dictionary
                self.posts[post_id] = parsed_post

                # Group posts by related child profiles
                related_profiles = post.get("relatedProfiles", [])
                for profile in related_profiles:
                    child_id = str(profile.get("id", ""))
                    if child_id:
                        if child_id not in self.posts_by_child:
                            self.posts_by_child[child_id] = []
                        self.posts_by_child[child_id].append(parsed_post)

            _LOGGER.info(
                f"POSTS: Successfully parsed {len(self.posts)} posts for {len(self.posts_by_child)} children"
            )

        except Exception as e:
            _LOGGER.error(f"POSTS: Error parsing posts data: {type(e).__name__}")
            self.posts = {}
            self.posts_by_child = {}

    def _extract_post_content(self, content):
        """Extract and clean post content"""
        if isinstance(content, dict):
            html_content = content.get("html", "")
            # Strip HTML tags for clean text
            clean_content = re.sub(r"<[^>]+>", "", html_content)
            clean_content = re.sub(r"\s+", " ", clean_content).strip()
            return {
                "html": html_content,
                "text": clean_content,
            }
        return {"html": "", "text": ""}

    def _extract_post_owner(self, owner_profile):
        """Extract post owner information"""
        if not owner_profile:
            return {}

        return {
            "id": str(owner_profile.get("id", "")),
            "name": owner_profile.get("fullName", ""),
            "short_name": owner_profile.get("shortName", ""),
            "role": owner_profile.get("role", ""),
            "institution": owner_profile.get("institution", {}).get(
                "institutionName", ""
            ),
            "institution_code": owner_profile.get("institution", {}).get(
                "institutionCode", ""
            ),
            "metadata": owner_profile.get("metadata", ""),
        }

    def _extract_post_attachments(self, attachments):
        """Extract post attachments (files, media, documents)"""
        extracted_attachments = []

        for attachment in attachments:
            attachment_info = {
                "id": str(attachment.get("id", "")),
                "name": attachment.get("name", ""),
                "status": attachment.get("status", ""),
                "type": "unknown",
            }

            # Handle file attachments
            if "file" in attachment and attachment["file"]:
                file_info = attachment["file"]
                attachment_info.update(
                    {
                        "type": "file",
                        "url": file_info.get("url", ""),
                        "created": file_info.get("created", ""),
                        "bucket": file_info.get("bucket", ""),
                        "key": file_info.get("key", ""),
                    }
                )

            # Handle media attachments (images, videos)
            elif "media" in attachment and attachment["media"]:
                media_info = attachment["media"]
                attachment_info.update(
                    {
                        "type": "media",
                        "media_type": media_info.get("mediaType", ""),
                        "url": media_info.get("file", {}).get("url", ""),
                        "thumbnail_url": media_info.get("thumbnailUrl", ""),
                        "duration": media_info.get("duration"),
                        "tags": media_info.get("tags", []),
                    }
                )

            # Handle document attachments
            elif "document" in attachment and attachment["document"]:
                attachment_info.update(
                    {
                        "type": "document",
                        "url": attachment["document"].get("url", ""),
                    }
                )

            # Handle link attachments
            elif "link" in attachment and attachment["link"]:
                attachment_info.update(
                    {
                        "type": "link",
                        "url": attachment["link"].get("url", ""),
                    }
                )

            extracted_attachments.append(attachment_info)

        return extracted_attachments

    def _extract_shared_groups(self, shared_groups):
        """Extract groups the post is shared with"""
        groups = []
        for group in shared_groups:
            groups.append(
                {
                    "id": str(group.get("id", "")),
                    "name": group.get("name", ""),
                    "short_name": group.get("shortName", ""),
                    "institution_code": group.get("institutionCode", ""),
                    "institution_name": group.get("institutionName", ""),
                    "is_main_group": group.get("mainGroup", False),
                    "portal_roles": group.get("portalRoles", []),
                }
            )
        return groups

    def _extract_related_profiles(self, related_profiles):
        """Extract profiles related to the post (usually children)"""
        profiles = []
        for profile in related_profiles:
            profiles.append(
                {
                    "id": str(profile.get("id", "")),
                    "name": profile.get("fullName", ""),
                    "short_name": profile.get("shortName", ""),
                    "role": profile.get("role", ""),
                    "institution_code": profile.get("institution", {}).get(
                        "institutionCode", ""
                    ),
                    "institution_name": profile.get("institution", {}).get(
                        "institutionName", ""
                    ),
                    "main_group": profile.get("mainGroupName", ""),
                }
            )
        return profiles
