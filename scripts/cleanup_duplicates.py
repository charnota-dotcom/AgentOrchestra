import json
import sqlite3
import os
from pathlib import Path

def get_data_dir():
    # Matches apps/service/main.py logic
    home = Path.home()
    return home / ".local" / "share" / "agentorchestra"

def cleanup_annotations():
    print("--- Cleaning up annotations.json ---")
    data_dir = get_data_dir() / "annotations"
    json_path = data_dir / "annotations.json"
    
    if not json_path.exists():
        print(f"Skipping: {json_path} does not exist.")
        return

    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {json_path}: {e}")
        return

    if not isinstance(data, list):
        print("Error: annotations.json is not a list.")
        return

    before_count = len(data)
    seen = set()
    clean = []
    
    for ann in data:
        key = (
            ann.get("screen_name", ""),
            ann.get("widget_path", ""),
            ann.get("text_snippet", ""),
            ann.get("comment", "")
        )
        if key not in seen:
            seen.add(key)
            clean.append(ann)
    
    after_count = len(clean)
    if before_count == after_count:
        print("No duplicate annotations found.")
    else:
        print(f"Removed {before_count - after_count} duplicate annotations.")
        try:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(clean, f, indent=2)
            print("Successfully updated annotations.json.")
        except Exception as e:
            print(f"Error writing {json_path}: {e}")

def cleanup_action_log():
    print("\n--- Cleaning up action log ---")
    log_dir = get_data_dir() / "action_logs"
    log_path = log_dir / "agentorchestra.json"
    
    if not log_path.exists():
        print(f"Skipping: {log_path} does not exist.")
        return

    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"Error reading {log_path}: {e}")
        return

    if not isinstance(data, dict):
        print("Error: action log is not a dictionary.")
        return

    total_removed = 0
    new_data = {}
    for idx_str, attempts in data.items():
        if not isinstance(attempts, list):
            new_data[idx_str] = attempts
            continue
        
        seen = set()
        clean_attempts = []
        for att in attempts:
            key = (
                att.get("description", ""),
                tuple(att.get("change_overview", []))
            )
            if key not in seen:
                seen.add(key)
                clean_attempts.append(att)
            else:
                total_removed += 1
        new_data[idx_str] = clean_attempts

    if total_removed == 0:
        print("No duplicate action log entries found.")
    else:
        print(f"Removed {total_removed} duplicate action log entries.")
        try:
            with open(log_path, 'w', encoding='utf-8') as f:
                json.dump(new_data, f, indent=2)
            print("Successfully updated action log.")
        except Exception as e:
            print(f"Error writing {log_path}: {e}")

def cleanup_drones():
    print("\n--- Cleaning up drone actions ---")
    data_dir = get_data_dir()
    db_path = data_dir / "agentorchestra.sqlite"
    
    if not db_path.exists():
        print(f"Skipping: {db_path} does not exist.")
        return

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
    except Exception as e:
        print(f"Error connecting to database: {e}")
        return

    try:
        cursor.execute("SELECT id FROM drone_blueprints WHERE name = 'Annotator'")
        row = cursor.fetchone()
        if not row:
            print("No 'Annotator' blueprint found. Skipping drone cleanup.")
            return
        blueprint_id = row['id']

        cursor.execute(
            "SELECT id, transcript, updated_at FROM drone_actions WHERE blueprint_id = ? ORDER BY updated_at DESC",
            (blueprint_id,)
        )
        actions = cursor.fetchall()
        print(f"Found {len(actions)} total actions for 'Annotator' blueprint.")

        seen_prompts = set()
        to_delete = []

        for action in actions:
            action_id = action['id']
            try:
                transcript = json.loads(action['transcript'])
                prompt = ""
                for msg in transcript:
                    if msg.get("role") == "user":
                        prompt = msg.get("content", "")
                        break
                
                if not prompt:
                    prompt = "__empty__"

                if prompt in seen_prompts:
                    to_delete.append(action_id)
                else:
                    seen_prompts.add(prompt)
            except Exception as e:
                print(f"Error parsing transcript for action {action_id}: {e}")
                continue

        if not to_delete:
            print("No duplicate drone actions found.")
        else:
            print(f"Removing {len(to_delete)} duplicate drone actions...")
            for aid in to_delete:
                cursor.execute("DELETE FROM drone_action_attachments WHERE action_id = ?", (aid,))
                cursor.execute("DELETE FROM drone_actions WHERE id = ?", (aid,))
            conn.commit()
            print("Successfully cleaned up drone actions.")

    except Exception as e:
        print(f"Database error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    cleanup_annotations()
    cleanup_action_log()
    cleanup_drones()
    print("\nCleanup complete. Please restart the application.")
