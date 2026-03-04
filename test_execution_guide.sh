#!/usr/bin/env bash
# test_execution_guide.sh - Guide for running comprehensive tests

echo "=============================================="
echo "Django API - Comprehensive Test Guide"
echo "=============================================="
echo ""

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}Prerequisites:${NC}"
echo "1. Virtual environment activated"
echo "2. Dependencies installed: pip install -r requirements.txt"
echo "3. Database running: docker-compose up db -d"
echo "4. Migrations applied: python manage.py migrate"
echo ""

echo -e "${BLUE}Test Execution Options:${NC}"
echo ""

echo "1. Run all tests using Django test runner:"
echo "   ${YELLOW}python manage.py test${NC}"
echo "   Expected: All tests pass"
echo ""

echo "2. Run all tests using pytest:"
echo "   ${YELLOW}pytest${NC}"
echo "   Expected: All tests pass with verbose output"
echo ""

echo "3. Run specific app tests:"
echo "   ${YELLOW}pytest products/tests.py -v${NC}"
echo "   ${YELLOW}pytest orders/tests.py -v${NC}"
echo "   ${YELLOW}pytest authentication/tests.py -v${NC}"
echo "   ${YELLOW}pytest shipments/tests.py -v${NC}"
echo "   ${YELLOW}pytest requests/tests.py -v${NC}"
echo "   ${YELLOW}pytest invitations/tests.py -v${NC}"
echo ""

echo "4. Run specific test class:"
echo "   ${YELLOW}pytest products/tests.py::WarehouseAPITest -v${NC}"
echo "   ${YELLOW}pytest orders/tests.py::OrderAPITest -v${NC}"
echo ""

echo "5. Run specific test method:"
echo "   ${YELLOW}pytest products/tests.py::WarehouseAPITest::test_create_warehouse -v${NC}"
echo ""

echo "6. Run tests with coverage:"
echo "   ${YELLOW}pytest --cov=. --cov-report=html${NC}"
echo "   Then open: htmlcov/index.html"
echo ""

echo "7. Run tests with output capture disabled (see print statements):"
echo "   ${YELLOW}pytest -s${NC}"
echo ""

echo "8. Run slow/integration tests only:"
echo "   ${YELLOW}pytest -m integration${NC}"
echo ""

echo -e "${BLUE}Test Execution Quick Commands:${NC}"
echo ""

echo "Run all tests quickly:"
echo "   ${YELLOW}python manage.py test --parallel${NC}"
echo ""

echo "Run tests with specific log level:"
echo "   ${YELLOW}python manage.py test --verbosity=2${NC}"
echo ""

echo "Keep test database after test run:"
echo "   ${YELLOW}python manage.py test --keepdb${NC}"
echo ""

echo -e "${BLUE}Expected Test Results:${NC}"
echo ""

echo "Authentication Tests (4 tests):"
echo "   ✓ test_user_registration"
echo "   ✓ test_user_login"
echo "   ✓ test_user_profile"
echo "   ✓ test_change_password"
echo ""

echo "Product Tests (7 tests):"
echo "   ✓ test_create_warehouse"
echo "   ✓ test_warehouse_str"
echo "   ✓ test_unique_warehouse_name"
echo "   ✓ test_create_product"
echo "   ✓ test_unique_sku_per_warehouse"
echo "   ✓ test_create_transfer"
echo "   ✓ test_mark_transfer_completed"
echo "   ✓ test_create_warehouse (API)"
echo "   ✓ test_list_warehouses"
echo "   ✓ test_warehouse_stats"
echo "   ✓ test_create_product (API)"
echo "   ✓ test_list_products"
echo "   ✓ test_update_product"
echo "   ✓ test_product_transfer"
echo ""

echo "Order Tests (7 tests):"
echo "   ✓ test_create_order"
echo "   ✓ test_unique_order_number"
echo "   ✓ test_create_order (API)"
echo "   ✓ test_list_orders"
echo "   ✓ test_ship_order"
echo "   ✓ test_deliver_order"
echo "   ✓ test_cancel_order"
echo "   ✓ test_cannot_cancel_shipped_order"
echo ""

echo "Shipment Tests (5 tests):"
echo "   ✓ test_create_shipment"
echo "   ✓ test_create_shipment (API)"
echo "   ✓ test_mark_in_transit"
echo "   ✓ test_mark_delivered"
echo ""

echo "Request Tests (6 tests):"
echo "   ✓ test_create_request"
echo "   ✓ test_create_request (API)"
echo "   ✓ test_list_requests"
echo "   ✓ test_approve_request"
echo "   ✓ test_reject_request"
echo ""

echo "Invitation Tests (7 tests):"
echo "   ✓ test_create_invitation"
echo "   ✓ test_create_invitation (API)"
echo "   ✓ test_accept_invitation"
echo "   ✓ test_reject_invitation"
echo "   ✓ test_accept_by_token"
echo "   ✓ test_expired_invitation"
echo ""

echo -e "${BLUE}Troubleshooting:${NC}"
echo ""

echo "Issue: 'No such table' error"
echo "Solution: Run migrations"
echo "   ${YELLOW}python manage.py migrate${NC}"
echo ""

echo "Issue: 'User matching query does not exist'"
echo "Solution: Tests create their own users in setUp()"
echo "   Make sure setUp() methods exist in test classes"
echo ""

echo "Issue: 'Port 5432 already in use'"
echo "Solution: Stop existing database container"
echo "   ${YELLOW}docker-compose down${NC}"
echo ""

echo "Issue: 'Authentication failed' in tests"
echo "Solution: Ensure client.force_authenticate() is called"
echo "   Make sure setUp() creates a test user"
echo ""

echo -e "${BLUE}Performance Tips:${NC}"
echo ""

echo "Run tests in parallel:"
echo "   ${YELLOW}pytest -n auto${NC}"
echo "   (Requires: pip install pytest-xdist)"
echo ""

echo "Run only fast tests (skip slow):"
echo "   ${YELLOW}pytest -m 'not slow'${NC}"
echo ""

echo "Run specific tests by pattern:"
echo "   ${YELLOW}pytest -k 'warehouse'${NC}"
echo ""

echo -e "${BLUE}Continuous Integration:${NC}"
echo ""

echo "For CI/CD pipelines use:"
echo "   ${YELLOW}pytest --cov=. --cov-report=xml --junit-xml=test-results.xml${NC}"
echo ""

echo "Exit codes:"
echo "   0  = All tests passed"
echo "   1  = Tests failed"
echo "   2  = Interrupted"
echo ""

echo -e "${GREEN}=============================================="
echo "Ready to run tests!"
echo "=============================================${NC}"
echo ""
echo "Start with: ${YELLOW}python manage.py test${NC}"
echo ""

