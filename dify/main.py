from dify_plugin import DifyPluginEnv, Plugin

# A command may run 600 seconds, plus time to create and delete its sandbox.
plugin = Plugin(DifyPluginEnv(MAX_REQUEST_TIMEOUT=720))

if __name__ == "__main__":
    plugin.run()
