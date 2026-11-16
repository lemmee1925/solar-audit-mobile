#!/usr/bin/env python3
"""
Script to create users for the Solar Audit application.
Usage: python create_admin.py [role]
       python create_admin.py         # Creates admin
       python create_admin.py admin   # Creates admin
       python create_admin.py engineer# Creates engineer
       python create_admin.py technician # Creates technician
"""

import sys
import getpass
from models import Session, User
from werkzeug.security import generate_password_hash
from site_config import load_site_config


def create_user(role='admin'):
    """Create a new user interactively."""
    valid_roles = ['admin', 'engineer', 'technician']
    
    if role not in valid_roles:
        print(f"Error: Invalid role '{role}'. Valid roles are: {', '.join(valid_roles)}")
        return
    
    role_description = {
        'admin': 'Full access - manage everything',
        'engineer': 'Create audits, manage plants/questions, view all reports',
        'technician': 'Limited access - create & view own audits only'
    }
    
    print(f"\n=== Create {role.upper()} User ===")
    print(f"   {role_description[role]}\n")
    
    username = input("Enter username: ").strip()
    if not username:
        print("Error: Username cannot be empty.")
        return
    
    full_name = input("Enter full name: ").strip()
    if not full_name:
        print("Error: Full name cannot be empty.")
        return
    
    password = getpass.getpass("Enter password: ")
    if not password:
        print("Error: Password cannot be empty.")
        return
    
    password_confirm = getpass.getpass("Confirm password: ")
    if password != password_confirm:
        print("Error: Passwords do not match.")
        return
    
    site = load_site_config()
    if site is None:
        print("Error: This install has not completed /setup yet — no site_config.json found. "
              "Run the app and complete the first-run setup wizard before using this script.")
        return

    session = Session()
    try:
        # Check if username already exists
        existing_user = session.query(User).filter_by(username=username).first()
        if existing_user:
            print(f"Error: User '{username}' already exists.")
            return
        
        # Create new user
        password_hash = generate_password_hash(password)
        new_user = User(
            username=username,
            full_name=full_name,
            password_hash=password_hash,
            role=role,
            is_active=True,
            site_id=site.site_id,
        )
        session.add(new_user)
        session.commit()
        
        print(f"\n✓ Success! User '{username}' has been created.")
        print(f"  Full Name: {full_name}")
        print(f"  Role: {role.upper()}\n")
    
    except Exception as e:
        session.rollback()
        print(f"Error: {str(e)}")
    finally:
        session.close()


if __name__ == '__main__':
    role = sys.argv[1].lower() if len(sys.argv) > 1 else 'admin'
    create_user(role)
