import re
from datetime import datetime

# Regex pattern to match a UTC timestamp in ISO 8601 format
utc_pattern = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$"
)

def is_utc_timestamp(value):
    """Check if the value is a valid UTC timestamp."""
    if isinstance(value, str):
        return bool(utc_pattern.match(value))
    return False

def mask_utc_timestamps(data):
    """Traverse and mask UTC timestamps in a JSON object."""
    if isinstance(data, dict):
        return {key: mask_utc_timestamps(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [mask_utc_timestamps(item) for item in data]
    elif is_utc_timestamp(data):
        return "2020-01-01T00:00:00"
    return data