# Licensed under MIT. | By Layeredy LLC (layeredy.com), a company by Auri (auri.lol) | github.com/layeredy/statusbot
import json
import requests
import discord
import asyncio
import time
from discord.ext import tasks, commands
from discord.ui import Button, View

class ServiceMonitor:
    def __init__(self, config_path):
        self.load_config(config_path)
        intents = discord.Intents.default()
        intents.message_content = True
        self.bot = commands.Bot(command_prefix='!', intents=intents)

        @self.bot.event
        async def on_ready():
            print(f'Logged in as {self.bot.user}')
            self.start_monitoring.start()

        @self.bot.command(name="set", description="Change the status of a monitor")
        async def set_status_command(ctx):
            await self.send_status_buttons(ctx)

        @self.bot.command(name="cycle", description="Add missing entries from config to statistics")
        async def cycle_command(ctx):
            await self.cycle_config_to_statistics(ctx)

        @self.bot.command(name="setm", description="Set maintenance status for a service")
        async def set_maintenance_command(ctx):
            await self.send_maintenance_buttons(ctx)

    async def send_status_buttons(self, ctx):
        if ctx.channel.id != int(self.channel_id):
            await ctx.send("This command can only be used in the specified channel.")
            return
        view = self.create_status_buttons()
        await ctx.send("Choose a monitor and status:", view=view)

    async def send_maintenance_buttons(self, ctx):
        if ctx.channel.id != int(self.channel_id):
            await ctx.send("This command can only be used in the specified channel.")
            return
        view = self.create_maintenance_buttons()
        await ctx.send("Choose a monitor to set maintenance status:", view=view)

    async def cycle_config_to_statistics(self, ctx):
        try:
            with open('statistics.json', 'r') as f:
                statistics = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            statistics = {}

        added = []
        for service in self.services:
            if service['name'] not in statistics:
                statistics[service['name']] = {"status": "Unknown", "timestamp": time.time()}
                added.append(service['name'])

        with open('statistics.json', 'w') as f:
            json.dump(statistics, f, indent=4)

        if added:
            await ctx.send(f"Added missing entries to statistics: {', '.join(added)}")
        else:
            await ctx.send("All entries in config are already in statistics.")

    def load_config(self, config_path):
        with open(config_path, 'r') as f:
            self.config = json.load(f)
        self.token = self.config['discord_token']
        self.channel_id = self.config['channel_id']
        self.ping_interval = self.config['ping_interval']
        self.services = self.config['services']
        self.status = {service['name']: True for service in self.services}
        self.prev_status = {service['name']: True for service in self.services}
        self.pending_resolutions = {service['name']: False for service in self.services}
        self.load_maintenance()

    def load_maintenance(self):
        try:
            with open('maintenance.json', 'r') as f:
                self.maintenance = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self.maintenance = {}

    def save_maintenance(self):
        with open('maintenance.json', 'w') as f:
            json.dump(self.maintenance, f, indent=4)

    async def send_message(self, embed, view=None):
        channel = self.bot.get_channel(int(self.channel_id))
        await channel.send(embed=embed, view=view)

    @tasks.loop(seconds=10)
    async def start_monitoring(self):
        for service in self.services:
            await self.check_service(service)
        await asyncio.sleep(self.ping_interval)

    async def check_service(self, service):
        try:
            response = requests.get(service['url'])
            if 'keyword' in service:
                if service['keyword'] not in response.text:
                    self.status[service['name']] = False
                else:
                    self.status[service['name']] = True
            elif 'status_code' in service:
                if response.status_code != service['status_code']:
                    self.status[service['name']] = False
                else:
                    self.status[service['name']] = True
        except Exception as e:
            self.status[service['name']] = False

        await self.handle_status_change(service)

    async def handle_status_change(self, service):
        current_status = self.status[service['name']]
        previous_status = self.prev_status[service['name']]

        if current_status != previous_status:
            if current_status:
                embed = discord.Embed(title=f"{service['name']} is back online!", color=discord.Color.green())
                await self.send_message(embed)
                self.pending_resolutions[service['name']] = False
            else:
                embed = discord.Embed(title=f"{service['name']} is offline!", color=discord.Color.red())
                view = self.create_buttons(service['name'])
                await self.send_message(embed, view)
                asyncio.create_task(self.auto_publish(service['name']))

        self.prev_status[service['name']] = current_status

    def create_buttons(self, service_name):
        view = View()
        button_ack = Button(label="Acknowledge", style=discord.ButtonStyle.primary)
        button_all_good = Button(label="All good!", style=discord.ButtonStyle.success)
        button_publish = Button(label="Publish", style=discord.ButtonStyle.danger)

        async def ack_callback(interaction):
            await interaction.response.send_message(f"Acknowledged: {service_name}", ephemeral=True)
            self.update_statistics(service_name, "Pending resolution")
            self.pending_resolutions[service_name] = True

        async def all_good_callback(interaction):
            await interaction.response.send_message(f"All good: {service_name}", ephemeral=True)
            self.update_statistics(service_name, "Operational")
            self.pending_resolutions[service_name] = False

        async def publish_callback(interaction):
            await self.send_status_buttons_interaction(interaction)

        button_ack.callback = ack_callback
        button_all_good.callback = all_good_callback
        button_publish.callback = publish_callback

        view.add_item(button_ack)
        view.add_item(button_all_good)
        view.add_item(button_publish)
        return view

    async def send_status_buttons_interaction(self, interaction):
        view = self.create_status_buttons()
        await interaction.response.send_message("Choose a monitor and status:", view=view, ephemeral=True)

    def create_publish_buttons(self, service_name):
        view = View()
        statuses = ["Operational", "Severe outage", "Full outage", "Degraded", "Maintenance"]

        for status in statuses:
            button = Button(label=status, style=discord.ButtonStyle.secondary)

            async def callback(interaction, status=status):
                await interaction.response.send_message(f"Status for {service_name} set to {status}", ephemeral=True)
                self.update_statistics(service_name, status)
                self.pending_resolutions[service_name] = False

            button.callback = callback
            view.add_item(button)

        return view

    def create_status_buttons(self):
        view = View()
        for service in self.services:
            button = Button(label=service['name'], style=discord.ButtonStyle.secondary)

            async def callback(interaction, service_name=service['name']):
                await interaction.response.send_message(f"Choose the status for {service_name}:", ephemeral=True, view=self.create_publish_buttons(service_name))

            button.callback = callback
            view.add_item(button)
        return view

    def create_maintenance_buttons(self):
        view = View()
        for service in self.services:
            button = Button(label=service['name'], style=discord.ButtonStyle.secondary)

            async def callback(interaction, service_name=service['name']):
                await interaction.response.send_message(f"Set maintenance status for {service_name}: on or off?", ephemeral=True, view=self.create_maintenance_toggle_buttons(service_name))

            button.callback = callback
            view.add_item(button)
        return view

    def create_maintenance_toggle_buttons(self, service_name):
        view = View()
        button_on = Button(label="On", style=discord.ButtonStyle.success)
        button_off = Button(label="Off", style=discord.ButtonStyle.danger)

        async def on_callback(interaction):
            self.maintenance[service_name] = True
            self.save_maintenance()
            await interaction.response.send_message(f"Maintenance for {service_name} is now ON.", ephemeral=True)

        async def off_callback(interaction):
            self.maintenance[service_name] = False
            self.save_maintenance()
            await interaction.response.send_message(f"Maintenance for {service_name} is now OFF.", ephemeral=True)

        button_on.callback = on_callback
        button_off.callback = off_callback

        view.add_item(button_on)
        view.add_item(button_off)
        return view

    async def auto_publish(self, service_name):
        await asyncio.sleep(300) 
        if not self.status[service_name] and not self.pending_resolutions[service_name]:
            embed = discord.Embed(title=f"Automatically published: {service_name} is potentially degraded", color=discord.Color.orange())
            await self.send_message(embed)
            self.update_statistics(service_name, "Auto published")

    def update_statistics(self, service_name, status):
        data = {}
        try:
            with open('statistics.json', 'r') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}

        data[service_name] = {"status": status, "timestamp": time.time()}

        with open('statistics.json', 'w') as f:
            json.dump(data, f, indent=4)

        self.update_history(service_name, status)

    def update_history(self, service_name, status):
        history = {}
        try:
            with open('history.json', 'r') as f:
                history = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            history = {}

        if service_name not in history:
            history[service_name] = []

        history[service_name].append({"status": status, "timestamp": time.time()})

        with open('history.json', 'w') as f:
            json.dump(history, f, indent=4)

    def run(self):
        self.bot.run(self.token)

if __name__ == '__main__':
    monitor = ServiceMonitor('config.json')
    monitor.run()

# Some code was debugged or fixed with the use of OpenAI's ChatGPT 4.0 & 4o 

# Licensed under MIT. | By Layeredy LLC (layeredy.com), a company by Auri (auri.lol) | github.com/layeredy/statusbot