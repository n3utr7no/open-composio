import httpx
from urllib.parse import quote

from ..core.registry import AppDefinition, ToolRegistry

APP = AppDefinition(
    id="weather",
    name="Weather Info",
    description="Get current weather information using wttr.in",
    auth_type="none",
)


async def get_weather(params: dict, auth_data: dict = None):
    location = params.get("location", "London")
    url = f"https://wttr.in/{quote(location)}?format=j1"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10.0)
            if response.status_code != 200:
                return {"error": f"Failed to fetch weather: HTTP {response.status_code}"}

            data = response.json()
            current = data.get("current_condition", [{}])[0]
            return {
                "location": location,
                "temperature_celsius": current.get("temp_C"),
                "description": current.get("weatherDesc", [{}])[0].get("value"),
                "humidity": current.get("humidity"),
                "raw": {
                    "windspeedKmph": current.get("windspeedKmph"),
                    "feelsLikeC": current.get("FeelsLikeC"),
                },
            }
    except Exception as e:
        # Fallback to plain text wttr.in format if json parsing fails
        try:
            async with httpx.AsyncClient() as client:
                res = await client.get(f"https://wttr.in/{quote(location)}?format=3", timeout=10.0)
                return {"weather": res.text.strip()}
        except Exception as ex:
            return {"error": f"Failed: {str(e)} / {str(ex)}"}


def register(registry: ToolRegistry) -> None:
    registry.register_app(APP.model_copy(deep=True))
    registry.register_action(
        app_id="weather",
        action_name="get_current",
        schema={
            "description": "Get the current temperature, weather conditions, and humidity for a given location.",
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "City or location name (e.g. New York, Tokyo, London)",
                }
            },
            "required": ["location"],
        },
        handler=get_weather,
    )
