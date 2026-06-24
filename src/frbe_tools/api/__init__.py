"""Typed access to the public FRBE/KBSB REST API.

The API splits endpoints into three auth tiers: ``anon`` (public), ``clb``
(club-level, bearer token), and ``mgmt`` (federation admin). Only ``anon``
endpoints are implemented today; :func:`frbe_tools.api.client.create_client`
already accepts an optional token for future ``clb``/``mgmt`` work.
"""

from __future__ import annotations
