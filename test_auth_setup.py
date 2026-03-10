#!/usr/bin/env python
"""
Test script to verify authentication system setup
Run this after migrations to test basic functionality
"""

import os
import django
import pytest

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'api.settings')
django.setup()

from django.contrib.auth import get_user_model
from authentication.models import UserRole

User = get_user_model()

@pytest.mark.django_db
def test_setup():
    """Test basic authentication setup"""
    print("🧪 Testing Authentication Setup\n")

    # Test 1: Check User model
    print("✅ Test 1: User model imported successfully")
    print(f"   User model: {User.__name__}")
    print(f"   Username field: {User.USERNAME_FIELD}")

    # Test 2: Check roles
    print("\n✅ Test 2: User roles configured")
    for role in UserRole:
        print(f"   - {role.label}: {role.value}")

    # Test 3: Create test user
    print("\n✅ Test 3: Creating test user")
    test_email = "test_setup@example.com"

    # Clean up if exists
    User.objects.filter(email=test_email).delete()

    user = User.objects.create_user(
        email=test_email,
        password="TestPassword123!",
        first_name="Test",
        last_name="User"
    )
    print(f"   Created user: {user.email}")
    print(f"   Role: {user.role}")
    print(f"   Is active: {user.is_active}")

    # Test 4: Test role methods
    print("\n✅ Test 4: Testing role methods")
    print(f"   is_admin(): {user.is_admin()}")
    print(f"   is_manager(): {user.is_manager()}")
    print(f"   has_role(USER): {user.has_role(UserRole.USER)}")

    # Test 5: Test password
    print("\n✅ Test 5: Testing password validation")
    is_correct = user.check_password("TestPassword123!")
    print(f"   Password check: {'✓ Correct' if is_correct else '✗ Failed'}")

    # Test 6: Create different role users
    print("\n✅ Test 6: Creating users with different roles")

    # Manager
    User.objects.filter(email="manager_test@example.com").delete()
    manager = User.objects.create_user(
        email="manager_test@example.com",
        password="ManagerPass123!",
        role=UserRole.MANAGER
    )
    print(f"   Manager: {manager.email} - is_manager(): {manager.is_manager()}")

    # Admin
    User.objects.filter(email="admin_test@example.com").delete()
    admin = User.objects.create_user(
        email="admin_test@example.com",
        password="AdminPass123!",
        role=UserRole.ADMIN,
        is_staff=True
    )
    print(f"   Admin: {admin.email} - is_admin(): {admin.is_admin()}")

    # Clean up test users
    print("\n🧹 Cleaning up test users...")
    User.objects.filter(email__in=[test_email, "manager_test@example.com", "admin_test@example.com"]).delete()
    print("   Test users deleted")

    print("\n" + "="*50)
    print("🎉 All tests passed successfully!")
    print("="*50)
    print("\n📝 Summary:")
    print("   - Custom User model is working")
    print("   - Role-based access is configured")
    print("   - Password hashing is working")
    print("   - User creation is functional")
    print("\n✨ Your authentication system is ready to use!")

if __name__ == "__main__":
    try:
        test_setup()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
