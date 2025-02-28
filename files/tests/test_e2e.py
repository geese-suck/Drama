from bs4 import BeautifulSoup
from time import time, sleep
from files.__main__ import app

# these tests require `docker-compose up` first

def test_rules():
	response = app.test_client().get("/sidebar")
	assert response.status_code == 200
	assert response.text.startswith("<!DOCTYPE html>")


def test_signup():
	client = app.test_client()
	with client: # this keeps the session between requests, which we need
		signup_get_response = client.get("/signup")
		assert signup_get_response.status_code == 200
		soup = BeautifulSoup(signup_get_response.text, 'html.parser')
		# these hidden input values seem to be used for anti-bot purposes and need to be submitted
		formkey = next(tag for tag in soup.find_all("input") if tag.get("name") == "formkey").get("value")
		form_timestamp = next(tag for tag in soup.find_all("input") if tag.get("name") == "now").get("value")

		sleep(5) # too-fast submissions are rejected (bot check?)
		username = "testuser" + str(round(time()))
		signup_post_response = client.post("/signup", data={
			"username": username,
			"password": "password",
			"password_confirm": "password",
			"email": "",
			"formkey": formkey,
			"now": form_timestamp
		})
		print(f"Signing up as {username}", flush=True)
		assert signup_post_response.status_code == 302
		assert "error" not in signup_post_response.location

		# we should now be logged in and able to post
