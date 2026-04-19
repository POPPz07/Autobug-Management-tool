"""Mock LLM for testing without API keys. Returns realistic hardcoded responses."""

import json


class MockResponse:
    """Mimics a LangChain AI message response."""

    def __init__(self, content: str):
        self.content = content


class MockLLM:
    """Mock LLM that returns predefined responses based on prompt content."""

    def invoke(self, prompt: str) -> MockResponse:
        """Return a realistic mock response based on the prompt type."""
        if "Analyse the bug report" in prompt or "Analyze the bug report" in prompt:
            return MockResponse(json.dumps({
                "inferred_steps": [
                    "Navigate to the login page",
                    "Enter valid username in the username field",
                    "Enter valid password in the password field",
                    "Click the Login/Submit button",
                    "Observe the error message displayed"
                ],
                "target_elements": [
                    "#username",
                    "#password",
                    "#submit",
                    "#error"
                ],
                "expected_behavior": "User should be logged in and redirected to dashboard after entering correct credentials",
                "success_condition": "The text 'Invalid credentials' appears on screen after submitting valid login credentials, proving the bug is reproduced",
                "risk_factors": [
                    "Form may use AJAX instead of traditional submit",
                    "Error element may take time to appear",
                    "CSRF tokens might be required"
                ]
            }, indent=2))

        if "Write a Python script" in prompt or "Selenium automation expert" in prompt:
            return MockResponse(self._generate_script(prompt))

        if "previously generated" in prompt or "PREVIOUS SCRIPT" in prompt:
            return MockResponse(
                "The script failed because the URL was not accessible from inside the container. "
                "Fixed by using the correct host URL.\n\n"
                + self._generate_script(prompt)
            )

        return MockResponse('{"error": "unknown prompt type"}')

    def _generate_script(self, prompt: str) -> str:
        """Generate a mock Selenium script."""
        # Extract target URL from prompt if present
        import re
        url_match = re.search(r'Target URL:\s*(\S+)', prompt)
        target_url = url_match.group(1) if url_match else "http://host.docker.internal:8080/login"

        return f'''import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

driver = None
try:
    print("Step 1: Setting up Chrome driver")
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.binary_location = "/usr/bin/chromium"
    service = Service("/usr/bin/chromedriver")
    driver = webdriver.Chrome(service=service, options=options)

    print("Step 2: Navigating to login page")
    driver.get("{target_url}")

    print("Step 3: Entering username")
    username_field = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "username"))
    )
    username_field.send_keys("testuser")

    print("Step 4: Entering password")
    password_field = driver.find_element(By.ID, "password")
    password_field.send_keys("correctpassword123")

    print("Step 5: Clicking submit button")
    submit_button = driver.find_element(By.ID, "submit")
    submit_button.click()

    print("Step 6: Checking for error message")
    error_element = WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.ID, "error"))
    )

    if "Invalid credentials" in error_element.text:
        print("Bug confirmed: Login shows Invalid credentials even with correct credentials")
        print("REPRODUCED")
    else:
        raise AssertionError(f"Expected 'Invalid credentials' but got: {{error_element.text}}")

except Exception as e:
    print(f"Error: {{e}}")
    if driver:
        driver.save_screenshot(f"/screenshots/failure_{{int(time.time())}}.png")
    raise
finally:
    if driver:
        driver.quit()
'''
