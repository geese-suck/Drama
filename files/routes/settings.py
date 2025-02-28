from __future__ import unicode_literals

import os
from shutil import copyfile

import pyotp
import requests
import youtube_dl

from files.helpers.actions import *
from files.helpers.alerts import *
from files.helpers.const import *
from files.helpers.get import *
from files.helpers.mail import *
from files.helpers.media import process_files, process_image
from files.helpers.regex import *
from files.helpers.sanitize import *
from files.helpers.sanitize import filter_emojis_only
from files.helpers.security import *
from files.helpers.useractions import *
from files.routes.wrappers import *

from .front import frontlist
from files.__main__ import app, cache, limiter


@app.get("/settings")
@auth_required
def settings(v):
	return redirect("/settings/personal")

@app.get("/settings/personal")
@auth_required
def settings_personal(v):
	return render_template("settings/personal.html", v=v)

@app.delete('/settings/background')
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def remove_background(v):
	if v.background:
		v.background = None
		g.db.add(v)
	return {"message": "Background removed!"}

@app.post("/settings/personal")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_personal_post(v):
	updated = False

	# begin common selectors #
	
	def update_flag(column_name:str, request_name:str):
		if not request.values.get(request_name, ''): return False
		request_flag = request.values.get(request_name, '') == 'true'
		if request_flag != getattr(v, column_name):
			setattr(v, column_name, request_flag)
			return True
		return False
	
	def update_potentially_permanent_flag(column_name:str, request_name:str, friendly_name:str, badge_id:Optional[int]):
		if not request.values.get(request_name): return False
		current_value = getattr(v, column_name)
		if FEATURES['USERS_PERMANENT_WORD_FILTERS'] and current_value > 1:
			abort(403, f"Cannot change the {friendly_name} setting after you've already set it permanently!")
		request_flag = int(request.values.get(request_name, '') == 'true')
		if current_value and request_flag and request.values.get("permanent", '') == 'true' and request.values.get("username") == v.username:
			if v.client: abort(403, f"Cannot set {friendly_name} permanently from the API")
			request_flag = int(time.time())
			setattr(v, column_name, request_flag)
			if badge_id: badge_grant(v, badge_id)
			return render_template("settings/personal.html", v=v, msg=f"You have set the {friendly_name} permanently! Enjoy your new badge!")
		elif current_value != request_flag:
			setattr(v, column_name, request_flag)
			return True
		return False

	def set_selector_option(column_name:str, api_name:str, valid_values:Iterable[str], error_msg:str="value"):
		opt = request.values.get(api_name)
		if opt: opt = opt.strip()
		if not opt: return False
		if opt in valid_values:
			setattr(v, column_name, opt)
			return True
		abort(400, f"'{opt}' is not a valid {error_msg}")

	# end common selectors #

	background = request.values.get("background", v.background)
	if background != v.background and background.endswith(".webp") and len(background) <= 20:
		v.background = request.values.get("background")
		updated = True
	elif request.values.get("reddit", v.reddit) != v.reddit:
		reddit = request.values.get("reddit")
		if reddit in {'old.reddit.com', 'reddit.com', 'i.reddit.com', 'teddit.net', 'libredd.it', 'unddit.com'}:
			updated = True
			v.reddit = reddit
	elif request.values.get("poor", v.poor) != v.poor:
		updated = True
		v.poor = request.values.get("poor", v.poor) == 'true'
		session['poor'] = v.poor
	
	slur_filter_updated = updated or update_potentially_permanent_flag("slurreplacer", "slurreplacer", "slur replacer", 192)
	if isinstance(slur_filter_updated, bool):
		updated = slur_filter_updated
	else:
		g.db.add(v)
		return slur_filter_updated
	
	profanity_filter_updated = updated or update_potentially_permanent_flag("profanityreplacer", "profanityreplacer", "profanity replacer", 190)
	if isinstance(profanity_filter_updated, bool):
		updated = profanity_filter_updated
	else:
		g.db.add(v)
		return profanity_filter_updated

	updated = updated or update_flag("hidevotedon", "hidevotedon")
	updated = updated or update_flag("cardview", "cardview")
	updated = updated or update_flag("highlightcomments", "highlightcomments")
	updated = updated or update_flag("newtab", "newtab")
	updated = updated or update_flag("newtabexternal", "newtabexternal")
	updated = updated or update_flag("nitter", "nitter")
	updated = updated or update_flag("imginn", "imginn")
	updated = updated or update_flag("controversial", "controversial")
	updated = updated or update_flag("sigs_disabled", "sigs_disabled")
	updated = updated or update_flag("over_18", "over_18")
	updated = updated or update_flag("is_private", "private")

	if not updated and request.values.get("spider", v.spider) != v.spider and v.spider <= 1:
		updated = True
		v.spider = int(request.values.get("spider") == 'true')
		if v.spider: badge_grant(user=v, badge_id=179)
		else: 
			badge = v.has_badge(179)
			if badge: g.db.delete(badge)

	elif not updated and request.values.get("bio") == "":
		v.bio = None
		v.bio_html = None
		g.db.add(v)
		return render_template("settings/personal.html", v=v, msg="Your bio has been updated.")

	elif not updated and request.values.get("sig") == "":
		v.sig = None
		v.sig_html = None
		g.db.add(v)
		return render_template("settings/personal.html", v=v, msg="Your sig has been updated.")

	elif not updated and request.values.get("friends") == "":
		v.friends = None
		v.friends_html = None
		g.db.add(v)
		return render_template("settings/personal.html", v=v, msg="Your friends list has been updated.")

	elif not updated and request.values.get("enemies") == "":
		v.enemies = None
		v.enemies_html = None
		g.db.add(v)
		return render_template("settings/personal.html", v=v, msg="Your enemies list has been updated.")

	elif not updated and v.patron and request.values.get("sig"):
		sig = request.values.get("sig")[:200].replace('\n','').replace('\r','')
		sig_html = sanitize(sig)
		if len(sig_html) > 1000:
			return render_template("settings/personal.html",
								v=v,
								error="Your sig is too long")

		v.sig = sig[:200]
		v.sig_html=sig_html
		g.db.add(v)
		return render_template("settings/personal.html",
							v=v,
							msg="Your sig has been updated.")

	elif not updated and FEATURES['USERS_PROFILE_BODYTEXT'] and request.values.get("friends"):
		friends = request.values.get("friends")[:500]

		friends_html = sanitize(friends)

		if len(friends_html) > 2000:
			return render_template("settings/personal.html",
								v=v,
								error="Your friends list is too long")

		notify_users = NOTIFY_USERS(friends, v)

		if notify_users:
			cid = notif_comment(f"@{v.username} has added you to their friends list!")
			for x in notify_users:
				add_notif(cid, x)

		v.friends = friends[:500]
		v.friends_html=friends_html
		g.db.add(v)
		return render_template("settings/personal.html",
							v=v,
							msg="Your friends list has been updated.")


	elif not updated and FEATURES['USERS_PROFILE_BODYTEXT'] and request.values.get("enemies"):
		enemies = request.values.get("enemies")[:500]

		enemies_html = sanitize(enemies)

		if len(enemies_html) > 2000:
			return render_template("settings/personal.html",
								v=v,
								error="Your enemies list is too long")

		notify_users = NOTIFY_USERS(enemies, v)
		if notify_users:
			cid = notif_comment(f"@{v.username} has added you to their enemies list!")
			for x in notify_users:
				add_notif(cid, x)

		v.enemies = enemies[:500]
		v.enemies_html=enemies_html
		g.db.add(v)
		return render_template("settings/personal.html",
							v=v,
							msg="Your enemies list has been updated.")


	elif not updated and FEATURES['USERS_PROFILE_BODYTEXT'] and \
			(request.values.get("bio") or request.files.get('file')):
		bio = request.values.get("bio")[:1500]
		bio += process_files(request.files, v)
		bio = bio.strip()
		bio_html = sanitize(bio)

		if len(bio_html) > 10000:
			return render_template("settings/personal.html",
								v=v,
								error="Your bio is too long")

		if len(bio_html) > 10000: abort(400)

		v.bio = bio[:1500]
		v.bio_html=bio_html
		g.db.add(v)
		return render_template("settings/personal.html",
							v=v,
							msg="Your bio has been updated.")


	frontsize = request.values.get("frontsize")
	if frontsize:
		frontsize = int(frontsize)
		if frontsize in PAGE_SIZES:
			v.frontsize = frontsize
			updated = True
			cache.delete_memoized(frontlist)
		else: abort(400)
	
	updated = updated or set_selector_option("defaultsortingcomments", "defaultsortingcomments", COMMENT_SORTS, "comment sort")
	updated = updated or set_selector_option("defaultsorting", "defaultsorting", SORTS, "post sort")
	updated = updated or set_selector_option("defaulttime", "defaulttime", TIME_FILTERS, "time filter")

	theme = request.values.get("theme")
	if not updated and theme:
		if theme in THEMES:
			if theme == "transparent" and not v.background: 
				abort(409, "You need to set a background to use the transparent theme")
			v.theme = theme
			if theme == "win98": v.themecolor = "30409f"
			updated = True
		else: abort(400, f"{theme} is not a valid theme")

	house = request.values.get("house")
	if not updated and house and house in HOUSES and FEATURES['HOUSES']:
		if v.bite: abort(403)
		if v.house:
			if v.house.replace(' Founder', '') == house: abort(409, f"You're already in House {house}")
			cost = HOUSE_SWITCH_COST
		else: 
			cost = HOUSE_JOIN_COST

		success = v.charge_account('coins', cost)
		if not success:
			success = v.charge_account('marseybux', cost)
		if not success: abort(403)

		if house == "None": house = '' 
		v.house = house

		if v.house == "Vampire":
			send_repeatable_notification(DAD_ID, f"@{v.username} has joined House Vampire!")

		updated = True

	if updated:
		g.db.add(v)
		return {"message": "Your settings have been updated."}
	else:
		abort(400, "You didn't change anything.")


@app.post("/settings/filters")
@auth_required
def filters(v):
	filters=request.values.get("filters")[:1000].strip()

	if filters == v.custom_filter_list:
		return render_template("settings/advanced.html", v=v, error="You didn't change anything")

	v.custom_filter_list=filters
	g.db.add(v)
	return render_template("settings/advanced.html", v=v, msg="Your custom filters have been updated.")


def set_color(v:User, attr:str, color:Optional[str]):
	current = getattr(v, attr)
	color = color.strip().lower() if color else None
	if color:
		if color.startswith('#'): color = color[1:]
		if not color_regex.fullmatch(color):
			return render_template("settings/personal.html", v=v, error="Invalid color hex code!")
		if color and current != color:
			setattr(v, attr, color)
			g.db.add(v)
	return render_template("settings/personal.html", v=v, msg="Color successfully updated!")


@app.post("/settings/namecolor")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def namecolor(v):
	return set_color(v, "namecolor", request.values.get("namecolor"))
	
@app.post("/settings/themecolor")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def themecolor(v):
	return set_color(v, "themecolor", request.values.get("themecolor"))

@app.post("/settings/gumroad")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def gumroad(v):
	if GUMROAD_TOKEN == DEFAULT_CONFIG_VALUE: abort(404)
	if not (v.email and v.is_activated):
		abort(400, f"You must have a verified email to verify {patron} status and claim your rewards!")

	data = {'access_token': GUMROAD_TOKEN, 'email': v.email}
	response = requests.get('https://api.gumroad.com/v2/sales', data=data, timeout=5).json()["sales"]

	if len(response) == 0: abort(404, "Email not found")

	response = [x for x in response if x['variants_and_quantity']]
	response = response[0]
	tier = tiers[response["variants_and_quantity"]]
	if v.patron == tier: abort(400, f"{patron} rewards already claimed")

	marseybux = marseybux_li[tier] - marseybux_li[v.patron]
	if marseybux < 0: abort(400, f"{patron} rewards already claimed")

	existing = g.db.query(User.id).filter(User.email == v.email, User.is_activated == True, User.patron >= tier).first()
	if existing: abort(400, f"{patron} rewards already claimed on another account")

	v.patron = tier

	v.pay_account('marseybux', marseybux)
	send_repeatable_notification(v.id, f"You have received {marseybux} Marseybux! You can use them to buy awards in the [shop](/shop).")

	g.db.add(v)

	badge_grant(badge_id=20+tier, user=v)
	

	return {"message": f"{patron} rewards claimed!"}

@app.post("/settings/titlecolor")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def titlecolor(v):
	return set_color(v, "titlecolor", request.values.get("titlecolor"))

@app.post("/settings/verifiedcolor")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def verifiedcolor(v):
	if not v.verified: abort(403, "You don't have a checkmark")
	return set_color(v, "verifiedcolor", request.values.get("verifiedcolor"))

@app.post("/settings/security")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_security_post(v):
	if request.values.get("new_password"):
		if request.values.get("new_password") != request.values.get("cnf_password"):
			return render_template("settings/security.html", v=v, error="Passwords do not match.")

		if not valid_password_regex.fullmatch(request.values.get("new_password")):
			return render_template("settings/security.html", v=v, error="Password must be between 8 and 100 characters.")

		if not v.verifyPass(request.values.get("old_password")):
			return render_template("settings/security.html", v=v, error="Incorrect password")

		v.passhash = hash_password(request.values.get("new_password"))

		g.db.add(v)
		return render_template("settings/security.html", v=v, msg="Your password has been changed.")

	if request.values.get("new_email"):
		if not v.verifyPass(request.values.get('password')):
			return render_template("settings/security.html", v=v, error="Invalid password.")

		new_email = request.values.get("new_email","").strip().lower()

		if new_email == v.email:
			return render_template("settings/security.html", v=v, error="This email is already yours!")

		url = f"{SITE_FULL}/activate"

		now = int(time.time())

		token = generate_hash(f"{new_email}+{v.id}+{now}")
		params = f"?email={quote(new_email)}&id={v.id}&time={now}&token={token}"

		link = url + params

		send_mail(to_address=new_email,
				subject="Verify your email address.",
				html=render_template("email/email_change.html",
									action_url=link,
									v=v)
				)

		return render_template("settings/security.html", v=v, msg="Check your email and click the verification link to complete the email change.")

	if request.values.get("2fa_token"):
		if not v.verifyPass(request.values.get('password')):
			return render_template("settings/security.html", v=v, error="Invalid password or token.")

		secret = request.values.get("2fa_secret")
		x = pyotp.TOTP(secret)
		if not x.verify(request.values.get("2fa_token"), valid_window=1):
			return render_template("settings/security.html", v=v, error="Invalid password or token.")

		v.mfa_secret = secret
		g.db.add(v)
		return render_template("settings/security.html", v=v, msg="Two-factor authentication enabled.")

	if request.values.get("2fa_remove"):
		if not v.verifyPass(request.values.get('password')):
			return render_template("settings/security.html", v=v, error="Invalid password or token.")

		token = request.values.get("2fa_remove")

		if not v.validate_2fa(token):
			return render_template("settings/security.html", v=v, error="Invalid password or token.")

		v.mfa_secret = None
		g.db.add(v)
		g.db.commit()
		return render_template("settings/security.html", v=v, msg="Two-factor authentication disabled.")

@app.post("/settings/log_out_all_others")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_log_out_others(v):
	submitted_password = request.values.get("password", "").strip()
	if not v.verifyPass(submitted_password):
		return render_template("settings/security.html", v=v, error="Incorrect Password"), 401

	v.login_nonce += 1
	session["login_nonce"] = v.login_nonce
	g.db.add(v)
	return render_template("settings/security.html", v=v, msg="All other devices have been logged out")


@app.post("/settings/images/profile")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_images_profile(v):
	if g.is_tor: abort(403, "Image uploads are not allowed through TOR.")

	file = request.files["profile"]

	name = f'/images/{time.time()}'.replace('.','') + '.webp'
	file.save(name)
	highres = process_image(name, v)

	if not highres: abort(400)

	name2 = name.replace('.webp', 'r.webp')
	copyfile(name, name2)
	imageurl = process_image(name2, v, resize=100)

	if not imageurl: abort(400)

	if v.highres and '/images/' in v.highres:
		fpath = '/images/' + v.highres.split('/images/')[1]
		if path.isfile(fpath): os.remove(fpath)
	if v.profileurl and '/images/' in v.profileurl:
		fpath = '/images/' + v.profileurl.split('/images/')[1]
		if path.isfile(fpath): os.remove(fpath)
	v.highres = highres
	v.profileurl = imageurl
	g.db.add(v)


	return render_template("settings/personal.html", v=v, msg="Profile picture successfully updated.")


@app.post("/settings/images/banner")
@feature_required('USERS_PROFILE_BANNER')
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_images_banner(v):
	if g.is_tor: abort(403, "Image uploads are not allowed through TOR.")

	file = request.files["banner"]

	name = f'/images/{time.time()}'.replace('.','') + '.webp'
	file.save(name)
	bannerurl = process_image(name, v)

	if bannerurl:
		if v.bannerurl and '/images/' in v.bannerurl:
			fpath = '/images/' + v.bannerurl.split('/images/')[1]
			if path.isfile(fpath): os.remove(fpath)
		v.bannerurl = bannerurl
		g.db.add(v)

	return render_template("settings/personal.html", v=v, msg="Banner successfully updated.")

@app.get("/settings/css")
@auth_required
def settings_css_get(v):
	return render_template("settings/css.html", v=v)

@app.post("/settings/css")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_css(v):
	if v.agendaposter: abort(400, "Agendapostered users can't edit CSS!")
	css = request.values.get("css", v.css).strip().replace('\\', '').strip()[:4000]
	if '</style' in css.lower():
		abort(400, "Please message @Aevann if you get this error")
	v.css = css
	g.db.add(v)

	return render_template("settings/css.html", v=v, msg="Custom CSS successfully updated!")

@app.post("/settings/profilecss")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_profilecss(v):
	profilecss = request.values.get("profilecss", v.profilecss).strip().replace('\\', '').strip()[:4000]
	valid, error = validate_css(profilecss)
	if not valid:
		return render_template("settings/css.html", error=error, v=v)
	v.profilecss = profilecss
	g.db.add(v)
	return render_template("settings/css.html", v=v, msg="Profile CSS successfully updated!")

@app.get("/settings/security")
@auth_required
def settings_security(v):
	return render_template("settings/security.html",
						v=v,
						mfa_secret=pyotp.random_base32() if not v.mfa_secret else None,
						now=int(time.time())
						)

@app.post("/settings/block")
@limiter.limit("1/second;20/day")
@ratelimit_user("1/second;20/day")
@auth_required
def settings_block_user(v):
	user = get_user(request.values.get("username"), graceful=True)
	if not user: abort(404, "This user doesn't exist.")
	
	if user.unblockable:
		if not v.shadowbanned:
			send_notification(user.id, f"@{v.username} has tried to block you and failed because of your unblockable status!")
		abort(403, f"@{user.username} is unblockable!")

	if user.id == v.id: abort(400, "You can't block yourself")
	if user.id == AUTOJANNY_ID: abort(403, f"You can't block @{user.username}")
	if v.has_blocked(user): abort(409, f"You have already blocked @{user.username}")

	new_block = UserBlock(user_id=v.id, target_id=user.id)
	g.db.add(new_block)

	if user.admin_level >= PERMS['USER_BLOCKS_VISIBLE']:
		send_notification(user.id, f"@{v.username} has blocked you!")

	cache.delete_memoized(frontlist)
	return {"message": f"@{user.username} blocked."}


@app.post("/settings/unblock")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_unblock_user(v):
	user = get_user(request.values.get("username"))
	x = v.has_blocked(user)
	if not x: abort(409, "You can't unblock someone you haven't blocked")
	g.db.delete(x)
	if not v.shadowbanned and user.admin_level >= PERMS['USER_BLOCKS_VISIBLE']:
		send_notification(user.id, f"@{v.username} has unblocked you!")
	cache.delete_memoized(frontlist)
	return {"message": f"@{user.username} unblocked."}

@app.get("/settings/apps")
@auth_required
def settings_apps(v):
	return render_template("settings/apps.html", v=v)

@app.get("/settings/advanced")
@auth_required
def settings_advanced_get(v):
	return render_template("settings/advanced.html", v=v)

@app.post("/settings/name_change")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@is_not_permabanned
@ratelimit_user()
def settings_name_change(v):
	new_name=request.values.get("name").strip()

	if new_name==v.username:
		return render_template("settings/personal.html",
						v=v,
						error="You didn't change anything")

	if not valid_username_regex.fullmatch(new_name):
		return render_template("settings/personal.html",
						v=v,
						error="This isn't a valid username.")

	search_name = new_name.replace('\\', '').replace('_','\_').replace('%','')

	x = g.db.query(User).filter(
		or_(
			User.username.ilike(search_name),
			User.original_username.ilike(search_name)
			)
		).one_or_none()

	if x and x.id != v.id:
		return render_template("settings/personal.html",
						v=v,
						error=f"Username `{new_name}` is already in use.")

	v=get_account(v.id)
	v.username=new_name
	v.name_changed_utc=int(time.time())
	g.db.add(v)

	return render_template("settings/personal.html", v=v, msg="Name successfully changed!")

@app.post("/settings/song_change_mp3")
@feature_required('USERS_PROFILE_SONG')
@limiter.limit("3/second;10/day")
@ratelimit_user("3/second;10/day")
@auth_required
def settings_song_change_mp3(v):
	file = request.files['file']
	if file.content_type != 'audio/mpeg':
		return render_template("settings/personal.html", v=v, error="Not a valid MP3 file")

	song = str(time.time()).replace('.','')

	name = f'/songs/{song}.mp3'
	file.save(name)

	size = os.stat(name).st_size
	if size > 8 * 1024 * 1024:
		os.remove(name)
		return render_template("settings/personal.html", v=v, error="MP3 file must be smaller than 8MB")

	if path.isfile(f"/songs/{v.song}.mp3") and g.db.query(User).filter_by(song=v.song).count() == 1:
		os.remove(f"/songs/{v.song}.mp3")

	v.song = song
	g.db.add(v)

	return render_template("settings/personal.html", v=v, msg="Profile Anthem successfully updated!")

@app.post("/settings/song_change")
@feature_required('USERS_PROFILE_SONG')
@limiter.limit("3/second;10/day")
@ratelimit_user("3/second;10/day")
@auth_required
def settings_song_change(v):
	song=request.values.get("song").strip()

	if song == "" and v.song:
		if path.isfile(f"/songs/{v.song}.mp3") and g.db.query(User).filter_by(song=v.song).count() == 1:
			os.remove(f"/songs/{v.song}.mp3")
		v.song = None
		g.db.add(v)
		return render_template("settings/personal.html", v=v, msg="Profile Anthem successfully removed!")

	song = song.replace("https://music.youtube.com", "https://youtube.com")
	if song.startswith(("https://www.youtube.com/watch?v=", "https://youtube.com/watch?v=", "https://m.youtube.com/watch?v=")):
		id = song.split("v=")[1]
	elif song.startswith("https://youtu.be/"):
		id = song.split("https://youtu.be/")[1]
	else:
		return render_template("settings/personal.html", v=v, error="Not a youtube link.")

	if "?" in id: id = id.split("?")[0]
	if "&" in id: id = id.split("&")[0]

	if path.isfile(f'/songs/{id}.mp3'): 
		v.song = id
		g.db.add(v)
		return render_template("settings/personal.html", v=v, msg="Profile Anthem successfully updated!")
		
	
	req = requests.get(f"https://www.googleapis.com/youtube/v3/videos?id={id}&key={YOUTUBE_KEY}&part=contentDetails", timeout=5).json()
	duration = req['items'][0]['contentDetails']['duration']
	if duration == 'P0D':
		return render_template("settings/personal.html", v=v, error="Can't use a live youtube video!")

	if "H" in duration:
		return render_template("settings/personal.html", v=v, error="Duration of the video must not exceed 15 minutes.")

	if "M" in duration:
		duration = int(duration.split("PT")[1].split("M")[0])
		if duration > 15: 
			return render_template("settings/personal.html", v=v, error="Duration of the video must not exceed 15 minutes.")


	if v.song and path.isfile(f"/songs/{v.song}.mp3") and g.db.query(User).filter_by(song=v.song).count() == 1:
		os.remove(f"/songs/{v.song}.mp3")

	ydl_opts = {
		'outtmpl': '/songs/%(title)s.%(ext)s',
		'format': 'bestaudio/best',
		'postprocessors': [{
			'key': 'FFmpegExtractAudio',
			'preferredcodec': 'mp3',
			'preferredquality': '192',
		}],
	}

	with youtube_dl.YoutubeDL(ydl_opts) as ydl:
		try: ydl.download([f"https://youtube.com/watch?v={id}"])
		except Exception as e:
			print(e, flush=True)
			return render_template("settings/personal.html",
						v=v,
						error="Age-restricted videos aren't allowed.")

	files = os.listdir("/songs/")
	paths = [path.join("/songs/", basename) for basename in files]
	songfile = max(paths, key=path.getctime)
	os.rename(songfile, f"/songs/{id}.mp3")

	v.song = id
	g.db.add(v)
	return render_template("settings/personal.html", v=v, msg="Profile Anthem successfully updated!")

@app.post("/settings/title_change")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_title_change(v):
	if v.flairchanged: abort(403)
	
	customtitleplain = sanitize_settings_text(request.values.get("title"), 100)
	if customtitleplain == v.customtitleplain:
		return render_template("settings/personal.html", v=v, error="You didn't change anything")

	customtitle = filter_emojis_only(customtitleplain)
	customtitle = censor_slurs(customtitle, None)

	if len(customtitle) > 1000:
		return render_template("settings/personal.html", v=v, error="Flair too long!")

	v.customtitleplain = customtitleplain
	v.customtitle = customtitle
	g.db.add(v)

	return render_template("settings/personal.html", v=v, msg="Flair successfully updated!")


@app.post("/settings/pronouns_change")
@feature_required('PRONOUNS')
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_pronouns_change(v):
	pronouns = sanitize_settings_text(request.values.get("pronouns"))

	if len(pronouns) > 11:
		return render_template("settings/personal.html", v=v, error="Your pronouns exceed the character limit (11 characters)")

	if pronouns == v.pronouns:
		return render_template("settings/personal.html", v=v, error="You didn't change anything.")

	if not pronouns_regex.fullmatch(pronouns):
		return render_template("settings/personal.html", v=v, error="The pronouns you entered don't match the required format.")

	bare_pronouns = pronouns.lower().replace('/', '')
	if 'nig' in bare_pronouns: pronouns = 'BI/POC'
	elif 'fag' in bare_pronouns: pronouns = 'cute/twink'

	v.pronouns = pronouns
	g.db.add(v)

	return render_template("settings/personal.html", v=v, msg="Pronouns successfully updated!")


@app.post("/settings/checkmark_text")
@limiter.limit(DEFAULT_RATELIMIT_SLOWER)
@auth_required
@ratelimit_user()
def settings_checkmark_text(v):
	if not v.verified: abort(403)
	new_name = sanitize_settings_text(request.values.get("checkmark-text"), 100)
	if not new_name: abort(400)
	if new_name == v.verified: return render_template("settings/personal.html", v=v, error="You didn't change anything")
	v.verified = new_name
	g.db.add(v)
	return render_template("settings/personal.html", v=v, msg="Checkmark Text successfully updated!")
