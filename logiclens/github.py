"""GitHub OAuth (device flow), repo listing, and remote source sync without full clone."""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Any

# Draft for direct GitHub URL importing
