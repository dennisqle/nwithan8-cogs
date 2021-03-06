"""
Interact with a Plex Media Server, manage users
Copyright (C) 2019 Nathan Harris
"""

import discord
from discord.ext import commands, tasks
import requests
import asyncio
import time
from plexapi.server import PlexServer
import plexapi
from plex.db_commands import DB
from discord.ext import commands
import plex.settings as settings
import plex.plex_api as px

plex = px.plex

db = DB(settings.SQLITE_FILE, settings.MULTI_PLEX, (settings.TRIAL_LENGTH * 3600))


def trial_message(startOrStop, serverNumber=None):
    if startOrStop == 'start':
        return "Hello, welcome to {}! You have been granted a {}-hour trial!".format(
            settings.PLEX_SERVER_NAME[serverNumber] if serverNumber else settings.PLEX_SERVER_NAME[0],
            str(settings.TRIAL_LENGTH))
    else:
        return "Hello, your {}-hour trial of {} has ended".format(settings.TRIAL_LENGTH, settings.PLEX_SERVER_NAME[
            serverNumber] if serverNumber else settings.PLEX_SERVER_NAME[0])


async def add_to_plex(plexname, discordId, note, serverNumber=None):
    tempPlex = plex
    if serverNumber is not None:
        tempPlex = PlexServer(settings.PLEX_SERVER_URL[serverNumber], settings.PLEX_SERVER_TOKEN[serverNumber])
    try:
        if db.add_user_to_db(discordId, plexname, note, serverNumber):
            tempPlex.myPlexAccount().inviteFriend(user=plexname, server=tempPlex, sections=None, allowSync=False,
                                                  allowCameraUpload=False, allowChannels=False, filterMovies=None,
                                                  filterTelevision=None, filterMusic=None)
            await asyncio.sleep(30)
            px.add_to_tautulli(serverNumber)
            if note != 't':  # Trial members do not have access to Ombi
                px.add_to_ombi()
            return True
        else:
            print("{} could not be added to the database.".format(plexname))
            return False
    except Exception as e:
        print(e)
        return False


def delete_from_plex(id):
    tempPlex = plex;
    serverNumber = 0
    try:
        results = db.find_user_in_db("Plex", id)
        plexname = results[0]
        note = results[1]
        if settings.MULTI_PLEX:
            serverNumber = results[2]
            tempPlex = PlexServer(settings.PLEX_SERVER_URL[serverNumber], settings.PLEX_SERVER_TOKEN[serverNumber])
        if plexname is not None:
            tempPlex.myPlexAccount().removeFriend(user=plexname)
            if note != 't':
                px.delete_from_ombi(plexname)  # Error if trying to remove trial user that doesn't exist in Ombi?
            px.delete_from_tautulli(plexname, serverNumber)
            db.remove_user_from_db(id)
            return True, serverNumber
        else:
            return False, serverNumber
    except plexapi.exceptions.NotFound:
        # print("Not found")
        return False, serverNumber


def remove_nonsub(memberID):
    if memberID not in settings.EXEMPT_SUBS:
        delete_from_plex(memberID)


class PlexManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def purge_winners(self, ctx):
        try:
            results = db.getWinners()
            monitorlist = []
            for u in results:
                monitorlist.append(u[0])
            print("Winners: ")
            print(monitorlist)
            data = px.t_request("get_users_table", "length=1000")
            removed_list = ""
            error_message = ""
            for i in data['response']['data']['data']:
                try:
                    if str(i['friendly_name']) in monitorlist:
                        PlexUsername = (px.t_request("get_user", "user_id=" + str(i['user_id'])))['response']['data'][
                            'username']
                        if i['duration'] is None:
                            print(PlexUsername + " has not watched anything. Purging...")
                            mention_id = await self.remove_winner(str(PlexUsername))
                            removed_list = removed_list + (mention_id if mention_id is not None else "")
                        elif i['last_seen'] is None:
                            print(PlexUsername + " has never been seen. Purging...")
                            mention_id = await self.remove_winner(str(PlexUsername))
                            removed_list = removed_list + (mention_id if mention_id is not None else "")
                        elif i['duration'] / 3600 < settings.WINNER_THRESHOLD:
                            print(PlexUsername + " has NOT met the duration requirements. Purging...")
                            mention_id = await self.remove_winner(str(PlexUsername))
                            removed_list = removed_list + (mention_id if mention_id is not None else "")
                        elif time.time() - i['last_seen'] > 1209600:
                            print(PlexUsername + " last seen too long ago. Purging...")
                            mention_id = await self.remove_winner(str(PlexUsername))
                            removed_list = removed_list + (mention_id if mention_id is not None else "")
                        else:
                            print(PlexUsername + " has met the requirements, and will not be purged.")
                except Exception as e:
                    print(e)
                    error_message = error_message + "Error checking " + str(i['friendly_name']) + ". "
                    pass
            if removed_list != "":
                await ctx.send(removed_list + "You have been removed as a Winner due to inactivity.")
            else:
                await ctx.send("No winners purged.")
            if error_message != "":
                await ctx.send(error_message)
        except Exception as e:
            print(e)
            await ctx.send("Something went wrong. Please try again later.")

    async def remove_winner(self, username):
        id = db.find_user_in_db("Discord", username)[0]
        if id is not None:
            try:
                success, num = delete_from_plex(id)
                if success:
                    user = self.bot.get_user(int(id))
                    await user.create_dm()
                    await user.dm_channel.send(
                        "You have been removed from " + str(settings.PLEX_SERVER_NAME[num]) + " due to inactivity.")
                    await user.remove_roles(discord.utils.get(self.bot.get_guild(int(settings.DISCORD_SERVER_ID)).roles,
                                                              name=settings.WINNER_ROLE_NAME),
                                            reason="Inactive winner")
                    db.remove_user_from_db(id)
                    return "<@" + id + ">, "
            except plexapi.exceptions.BadRequest:
                return None
        else:
            return None

    @tasks.loop(seconds=settings.SUB_CHECK_TIME * (3600 * 24))
    async def check_subs(self):
        print("Checking Plex subs...")
        settings.EXEMPT_ROLES = []
        allRoles = self.bot.get_guild(int(settings.DISCORD_SERVER_ID)).roles
        for r in allRoles:
            if r.name in settings.SUB_ROLES:
                settings.EXEMPT_ROLES.append(r)
        for member in self.bot.get_guild(int(settings.DISCORD_SERVER_ID)).members:
            if not any(x in member.roles for x in settings.EXEMPT_ROLES):
                remove_nonsub(member.id)
        print("Plex subs check complete.")

    @tasks.loop(seconds=settings.TRIAL_CHECK_FREQUENCY * 60)
    async def check_trials(self):
        print("Checking Plex trials...")
        trials = db.getTrials()
        trial_role = discord.utils.get(self.bot.get_guild(int(settings.DISCORD_SERVER_ID)).roles,
                                       name=settings.TRIAL_ROLE_NAME)
        for u in trials:
            print("Ending trial for " + str(u[0]))
            success, num = delete_from_plex(int(u[0]))
            if success:
                try:
                    user = self.bot.get_guild(int(settings.DISCORD_SERVER_ID)).get_member(int(u[0]))
                    await user.create_dm()
                    await user.dm_channel.send(trial_message('end', num))
                    await user.remove_roles(trial_role, reason="Trial has ended.")
                except Exception as e:
                    print(e)
                    print("Trial for Discord user " + str(u[0]) + " was ended, but user could not be notified.")
            else:
                print("Failed to remove Discord user " + str(u[0]) + " from Plex.")
        print("Plex trials check complete.")

    @commands.group(name="pm", aliases=["PM", "PlexMan", "plexman"], pass_context=True)
    async def pm(self, ctx: commands.Context):
        """
        Plex admin commands
        """
        if ctx.invoked_subcommand is None:
            await ctx.send("What subcommand?")

    @pm.command(name="access", pass_context=True)
    # Anyone can use this command
    async def pm_access(self, ctx: commands.Context, PlexUsername: str = None):
        """
        Check if you or another user has access to the Plex server
        """
        hasAccess = False
        serverNumber = 0
        if PlexUsername is None:
            name = db.find_user_in_db("Plex", ctx.message.author.id)[0]
        else:
            name = PlexUsername
        if name is not None:
            if settings.MULTI_PLEX:
                for i in range(0, len(settings.PLEX_SERVER_URL)):
                    tempPlex = PlexServer(settings.PLEX_SERVER_URL[i], settings.PLEX_SERVER_TOKEN[i])
                    for u in tempPlex.myPlexAccount().users():
                        if u.username == name:
                            for s in u.servers:
                                if s.name == settings.PLEX_SERVER_NAME[i] or s.name == settings.PLEX_SERVER_ALT_NAME[i]:
                                    hasAccess = True
                                    serverNumber = i
                                    break
                            break
                    break
            else:
                for u in plex.myPlexAccount().users():
                    if u.username == name:
                        for s in u.servers:
                            if s.name == settings.PLEX_SERVER_NAME[0] or s.name == settings.PLEX_SERVER_ALT_NAME[0]:
                                hasAccess = True
                                break
                        break
            if hasAccess:
                await ctx.send(("You have" if PlexUsername is None else name + " has") + " access to " + (
                    settings.PLEX_SERVER_NAME[serverNumber] if settings.MULTI_PLEX else settings.PLEX_SERVER_NAME[0]))
            else:
                await ctx.send(
                    ("You do not have" if PlexUsername is None else name + " does not have") + " access to " + (
                        "any of the Plex servers" if settings.MULTI_PLEX else settings.PLEX_SERVER_NAME[0]))
        else:
            await ctx.send("User not found.")

    @pm_access.error
    async def pm_access_error(self, ctx, error):
        print(error)
        await ctx.send("Sorry, something went wrong.")

    @pm.command(name="status", aliases=['ping', 'up', 'online'], pass_context=True)
    async def pm_status(self, ctx: commands.Context):
        """
        Check if the Plex server(s) is/are online
        """
        status = ""
        if settings.MULTI_PLEX:
            for i in range(0, len(settings.PLEX_SERVER_URL)):
                r = requests.get(settings.PLEX_SERVER_URL[i] + "/identity", timeout=10)
                if r.status_code != 200:
                    status = status + settings.PLEX_SERVER_NAME[i] + " is having connection issues right now.\n"
                else:
                    status = status + settings.PLEX_SERVER_NAME[i] + " is up and running.\n"
        else:
            r = requests.get(settings.PLEX_SERVER_URL[0] + "/identity", timeout=10)
            if r.status_code != 200:
                status = settings.PLEX_SERVER_NAME[0] + " is having connection issues right now."
            else:
                status = settings.PLEX_SERVER_NAME[0] + " is up and running."
        await ctx.send(status)

    @pm_status.error
    async def pm_status_error(self, ctx, error):
        print(error)
        await ctx.send("Sorry, I couldn't test the connection{}.".format('s' if settings.MULTI_PLEX else ""))

    @pm.command(name="winners", pass_context=True)
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_winners(self, ctx: commands.Context):
        """
        List winners' Plex usernames
        """
        try:
            winners = db.getWinners()
            response = "Winners:"
            for u in winners:
                response = response + "\n" + (u[0])
            await ctx.send(response)
        except Exception as e:
            await ctx.send("Error pulling winners from database.")

    @pm.command(name="purge", pass_context=True)
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_purge(self, ctx: commands.Context):
        """
        Remove inactive winners
        """
        await ctx.send("Purging winners...")
        await self.purge_winners(ctx)

    @pm.command(name="cleandb", aliases=["clean", "scrub", "syncdb"], pass_context=True)
    async def pm_cleandb(self, ctx: commands.Context):
        """
        Remove old users from database
        If you delete a user from Plex directly,
        run this to remove the user's entry in the
        Plex user database.
        """
        existingUsers = px.getPlexUsers()
        dbEntries = db.get_all_entries_in_db()
        if dbEntries:
            deletedUsers = ""
            for entry in dbEntries:
                if entry[1].lower() not in existingUsers:  # entry[1] is PlexUsername, compare lowercase to
                    # existingUsers (returned as lowercase)
                    deletedUsers += entry[1] + "\n"
                    print("Deleting " + str(entry[1]) + " from the Plex database...")
                    db.remove_user_from_db(entry[0])  # entry[0] is DiscordID
            if deletedUsers:
                await ctx.send("The following users were deleted from the database:\n" + deletedUsers[:-1])
            else:
                await ctx.send("No old users found and removed from database.")
        else:
            await ctx.send("An error occurred when grabbing users from the database.")

    @pm_cleandb.error
    async def pm_cleandb_error(self, ctx, error):
        print(error)
        await ctx.send("Something went wrong.")

    @pm.command(name="count")
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_count(self, ctx: commands.Context, serverNumber: int = None):
        """
        Check Plex share count
        Include optional serverNumber to check a specific Plex server (if using multiple servers)
        """
        if settings.MULTI_PLEX:
            if serverNumber is None:
                totals = ""
                for i in range(0, len(settings.PLEX_SERVER_URL)):
                    totals = totals + settings.PLEX_SERVER_NAME[i] + " has " + str(px.countServerSubs(i)) + " users\n"
                await ctx.send(totals)
            else:
                if serverNumber <= len(settings.PLEX_SERVER_URL):
                    await ctx.send(settings.PLEX_SERVER_NAME[serverNumber - 1] + " has " + str(
                        px.countServerSubs(serverNumber - 1)) + " users")
                else:
                    await ctx.send("That server number does not exist.")
        else:
            await ctx.send(settings.PLEX_SERVER_NAME[0] + " has " + str(px.countServerSubs(-1)) + " users")

    @pm_count.error
    async def pm_count_error(self, ctx, error):
        print(error)
        await ctx.send("Something went wrong. Please try again later.")

    @pm.command(name="add", alias=["invite", "new"], pass_context=True)
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_add(self, ctx: commands.Context, user: discord.Member, PlexUsername: str, serverNumber: int = None):
        """
        Add a Discord user to Plex
        Mention the Discord user and their Plex username
        Include optional serverNumber to add to a specific server (if using multiple Plex servers)
        """
        if settings.MULTI_PLEX:
            if serverNumber is None:  # No specific number indicated. Defaults adding to the least-fill server
                serverNumber = px.getSmallestServer()
            elif serverNumber > len(settings.PLEX_SERVER_URL):
                await ctx.send("That server number does not exist.")
            else:
                serverNumber = serverNumber - 1  # user's "server 5" is really server 4 in the index
            await ctx.send('Adding ' + PlexUsername + ' to ' + settings.PLEX_SERVER_NAME[
                serverNumber] + '. Please wait about 30 seconds...')
            try:
                added = await add_to_plex(PlexUsername, user.id, 's', serverNumber)
                if added:
                    role = discord.utils.get(ctx.message.guild.roles, name=settings.AFTER_APPROVED_ROLE_NAME)
                    await user.add_roles(role, reason="Access membership channels")
                    await ctx.send(
                        user.mention + " You've been invited, " + PlexUsername + ". Welcome to " +
                        settings.PLEX_SERVER_NAME[
                            serverNumber] + "!")
                else:
                    await ctx.send(user.name + " could not be added to that server.")
            except plexapi.exceptions.BadRequest:
                await ctx.send(PlexUsername + " is not a valid Plex username.")
        else:
            await ctx.send(
                'Adding ' + PlexUsername + ' to ' + settings.PLEX_SERVER_NAME[0] + '. Please wait about 30 seconds...')
            try:
                added = await add_to_plex(PlexUsername, user.id, 's', serverNumber)
                if added:
                    role = discord.utils.get(ctx.message.guild.roles, name=settings.AFTER_APPROVED_ROLE_NAME)
                    await user.add_roles(role, reason="Access membership channels")
                    await ctx.send(
                        user.mention + " You've been invited, " + PlexUsername + ". Welcome to " +
                        settings.PLEX_SERVER_NAME[0] + "!")
                else:
                    await ctx.send(user.name + " could not be added to Plex.")
            except plexapi.exceptions.BadRequest:
                await ctx.send(PlexUsername + " is not a valid Plex username.")

    @pm_add.error
    async def pm_add_error(self, ctx, error):
        print(error)
        await ctx.send("Please mention the Discord user to add to Plex, as well as their Plex username.")

    @pm.command(name="remove", alias=["uninvite", "delete", "rem"])
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_remove(self, ctx: commands.Context, user: discord.Member):
        """
        Remove a Discord user from Plex
        """
        deleted, num = delete_from_plex(user.id)
        if deleted:
            role = discord.utils.get(ctx.message.guild.roles, name=settings.AFTER_APPROVED_ROLE_NAME)
            await user.remove_roles(role, reason="Removed from Plex")
            await ctx.send("You've been removed from " + (
                settings.PLEX_SERVER_NAME[
                    num] if settings.MULTI_PLEX else settings.PLEX_SERVER_NAME[0]) + ", " + user.mention + ".")
        else:
            await ctx.send("User could not be removed.")

    @pm_remove.error
    async def pm_remove_error(self, ctx, error):
        print(error)
        await ctx.send("Please mention the Discord user to remove from Plex.")

    @pm.command(name="trial")
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_trial(self, ctx: commands.Context, user: discord.Member, PlexUsername: str, serverNumber: int = None):
        """
        Start a Plex trial
        """
        if settings.MULTI_PLEX:
            if serverNumber is None:  # No specific number indicated. Defaults adding to the least-fill server
                serverNumber = px.getSmallestServer()
            elif serverNumber > len(settings.PLEX_SERVER_URL):
                await ctx.send("That server number does not exist.")
            else:
                serverNumber = serverNumber - 1  # user's "server 5" is really server 4 in the index
            await ctx.send('Adding ' + PlexUsername + ' to ' + settings.PLEX_SERVER_NAME[
                serverNumber] + '. Please wait about 30 seconds...')
            try:
                added = await add_to_plex(PlexUsername, user.id, 't', serverNumber)
                if added:
                    role = discord.utils.get(ctx.message.guild.roles, name=settings.TRIAL_ROLE_NAME)
                    await user.add_roles(role, reason="Trial started.")
                    await user.create_dm()
                    await user.dm_channel.send(trial_message('start', serverNumber))
                    await ctx.send(
                        user.mention + ", your trial has begun. Please check your Direct Messages for details.")
                else:
                    await ctx.send(user.name + " could not be added to that server.")
            except plexapi.exceptions.BadRequest:
                await ctx.send(PlexUsername + " is not a valid Plex username.")
        else:
            await ctx.send(
                'Starting ' + settings.PLEX_SERVER_NAME[
                    0] + ' trial for ' + PlexUsername + '. Please wait about 30 seconds...')
            try:
                added = await add_to_plex(PlexUsername, user.id, 't')
                if added:
                    role = discord.utils.get(ctx.message.guild.roles, name=settings.TRIAL_ROLE_NAME)
                    await user.add_roles(role, reason="Trial started.")
                    await user.create_dm()
                    await user.dm_channel.send(trial_message('start'))
                    await ctx.send(
                        user.mention + ", your trial has begun. Please check your Direct Messages for details.")
                else:
                    await ctx.send(user.name + " could not be added to Plex.")
            except plexapi.exceptions.BadRequest:
                await ctx.send(PlexUsername + " is not a valid Plex username.")

    @pm_trial.error
    async def pm_trial_error(self, ctx, error):
        print(error)
        await ctx.send("Please mention the Discord user to add to Plex, as well as their Plex username.")

    @pm.command(name="import", pass_context=True)
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_import(self, ctx: commands.Context, user: discord.Member, PlexUsername: str, subType: str,
                        serverNumber: int = None):
        """
        Add existing Plex users to the database.
        user - tag a Discord user
        PlexUsername - Plex username or email of the Discord user
        subType - custom note for tracking subscriber type; MUST be less than 5 letters.
        Default in database: 's' for Subscriber, 'w' for Winner, 't' for Trial.
        NOTE: subType 't' will make a new 24-hour timestamp for the user.
        """
        if len(subType) > 4:
            await ctx.send("subType must be less than 5 characters long.")
        elif serverNumber is not None and serverNumber > len(settings.PLEX_SERVER_URL):
            await ctx.send("That server number does not exist.")
        else:
            new_entry = db.add_user_to_db(user.id, PlexUsername, subType, serverNumber)
            if new_entry:
                if subType == 't':
                    await ctx.send("Trial user was added/new timestamp issued.")
                else:
                    await ctx.send("User added to the database.")
            else:
                await ctx.send("User already exists in the database.")

    @pm_import.error
    async def pm_import_error(self, ctx, error):
        print(error)
        await ctx.send(
            "Please mention the Discord user to add to the database, including their Plex username and sub type.")

    @pm.group(name="find", aliases=["id"], pass_context=True)
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_find(self, ctx: commands.Context):
        """
        Find Discord or Plex user
        """
        if ctx.invoked_subcommand is None:
            await ctx.send("What subcommand?")

    @pm_find.command(name="plex", aliases=["p"])
    async def pm_find_plex(self, ctx: commands.Context, user: discord.Member):
        """
        Find Discord member's Plex username
        """
        results = db.find_user_in_db("Plex", user.id)
        name = results[0]
        note = results[1]
        num = None
        if settings.MULTI_PLEX:
            num = results[2]
        if name is not None:
            await ctx.send(user.mention + " is Plex user: " + name + (" [Trial" if note == 't' else " [Subscriber") + (
                " - Server " + str(num) if settings.MULTI_PLEX else "") + "]")
        else:
            await ctx.send("User not found.")

    @pm_find.command(name="discord", aliases=["d"])
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_find_discord(self, ctx: commands.Context, PlexUsername: str):
        """
        Find Plex user's Discord name
        """
        id = db.find_user_in_db("Discord", PlexUsername)[0]
        if id is not None:
            await ctx.send(PlexUsername + " is Discord user: " + self.bot.get_user(int(id)).mention)
        else:
            await ctx.send("User not found.")

    @pm_find.error
    async def pm_find_error(self, ctx, error):
        print(error)
        await ctx.send("An error occurred while looking for that user.")

    @pm.group(name="info")
    async def pm_info(self, ctx: commands.Context):
        """
        Get database entry for a user
        """
        if ctx.invoked_subcommand is None:
            await ctx.send("What subcommand?")

    @pm_info.command(name="plex", aliases=["p"])
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_info_plex(self, ctx, PlexUsername: str):
        """
        Get database entry for Plex username
        """
        embed = discord.Embed(title=("Info for " + str(PlexUsername)))
        n = db.describe_table("users")
        d = db.find_entry_in_db("PlexUsername", PlexUsername)
        if d:
            for i in range(0, len(n)):
                val = str(d[i])
                if str(n[i][1]) == "DiscordID":
                    val = val + " (" + self.bot.get_user(int(d[i])).mention + ")"
                if str(n[i][1]) == "Note":
                    val = ("Trial" if d[i] == 't' else "Subscriber")
                if settings.MULTI_PLEX and str(n[i][1]) == "ServerNum":
                    val = ("Server Number: " + d[i])
                if d[i] is not None:
                    embed.add_field(name=str(n[i][1]), value=val, inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send("That user is not in the database.")

    @pm_info.command(name="discord", aliases=["d"])
    @commands.has_role(settings.DISCORD_ADMIN_ROLE_NAME)
    async def pm_info_discord(self, ctx, user: discord.Member):
        """
        Get database entry for Discord user
        """
        embed = discord.Embed(title=("Info for " + user.name))
        n = db.describe_table("users")
        d = db.find_entry_in_db("DiscordID", user.id)
        if d:
            for i in range(0, len(n)):
                name = str(n[i][1])
                val = str(d[i])
                if str(n[i][1]) == "DiscordID":
                    val = val + " (" + self.bot.get_user(int(d[i])).mention + ")"
                if str(n[i][1]) == "Note":
                    val = ("Trial" if d[i] == 't' else "Subscriber")
                if settings.MULTI_PLEX and str(n[i][1]) == "ServerNum":
                    val = ("Server Number: " + d[i])
                if d[i] is not None:
                    embed.add_field(name=str(n[i][1]), value=val, inline=False)
            await ctx.send(embed=embed)
        else:
            await ctx.send("That user is not in the database.")

    @pm_info.error
    async def pm_info_error(self, ctx, error):
        print(error)
        await ctx.send("User not found.")

    @commands.Cog.listener()
    async def on_message(self, message):
        if settings.AUTO_WINNERS:
            if message.author.id == settings.GIVEAWAY_BOT_ID and "congratulations" in message.content.lower() and message.mentions:
                tempWinner = discord.utils.get(message.guild.roles, name=settings.TEMP_WINNER_ROLE_NAME)
                for u in message.mentions:
                    await u.add_roles(tempWinner, reason="Winner - access winner invite channel")
            if message.channel.id == settings.WINNER_CHANNEL and discord.utils.get(message.guild.roles,
                                                                                   name=settings.TEMP_WINNER_ROLE_NAME) in message.author.roles:
                plexname = message.content.strip()  # Only include username, nothing else
                await message.channel.send(
                    "Adding " + plexname + ". Please wait about 30 seconds...\n"
                                           "Be aware, you will be removed from this channel once you are added "
                                           "successfully.")
                try:
                    serverNumber = None
                    if settings.MULTI_PLEX:
                        serverNumber = px.getSmallestServer()
                    await add_to_plex(plexname, message.author.id, 'w', serverNumber)
                    await message.channel.send(
                        message.author.mention + " You've been invited, " + plexname + ". Welcome to " +
                        settings.PLEX_SERVER_NAME[serverNumber] + "!")
                    await message.author.remove_roles(
                        discord.utils.get(message.guild.roles, name=settings.TEMP_WINNER_ROLE_NAME),
                        reason="Winner was processed successfully.")
                except plexapi.exceptions.BadRequest:
                    await message.channel.send(
                        message.author.mention + ", " + plexname + " is not a valid Plex username.")

    @commands.Cog.listener()
    async def on_ready(self):
        self.check_trials.start()
        self.check_subs.start()

    def __init__(self, bot):
        self.bot = bot
        print("Plex Manager ready to go.")
