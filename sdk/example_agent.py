import sys
import os

# Allow importing local sdk package
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from client import OpenComposio

def main():
    print("Initializing OpenComposio Client...")
    client = OpenComposio(base_url="http://127.0.0.1:8000")
    
    # 1. List Apps
    print("\n--- Listing Available Apps ---")
    apps = client.get_apps()
    for app in apps:
        status = "Connected" if app["connected"] else "Disconnected"
        print(f"- {app['name']} (ID: {app['id']}) [{status}]")
        
    # 2. Get Weather Actions
    print("\n--- Fetching Weather Actions ---")
    actions = client.get_actions("weather")
    for action in actions:
        print(f"- {action['name']}: {action['description']}")
        print(f"  Params: {action['parameters_schema']}")
        
    # 3. Execute Weather Action (does not require auth)
    print("\n--- Running weather.get_current action ---")
    try:
        result = client.execute("weather", "get_current", {"location": "San Francisco"})
        print("Success! Response:")
        print(result)
    except Exception as e:
        print(f"Failed to execute weather action: {e}")

if __name__ == "__main__":
    main()
