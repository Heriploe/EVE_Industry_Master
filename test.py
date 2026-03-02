from Utilities.industry_cost import get_activity_cost, get_base_cost


if __name__ == "__main__":
    blueprint_id = 971
    runs = 1
    activity = "manufacturing"
    cost = get_activity_cost(blueprint_id=blueprint_id, runs=runs, activity=activity)
    base_cost = get_base_cost(blueprint_id=blueprint_id, activity=activity)
    print(f"blueprint_id={blueprint_id}, activity={activity}, runs={runs}, cost={cost},base_cost={base_cost}")
