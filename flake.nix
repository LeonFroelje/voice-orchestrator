{
  description = "Python devShells and App Package";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
    nixvim = {
      url = "github:nix-community/nixvim";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    nixvimModules = {
      url = "github:LeonFroelje/nixvim-modules";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixvim,
      nixvimModules,
    }:
    let
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};

      # 1. Define the Python version for packaging
      python = pkgs.python313;

      # 2. Define your dependencies list
      appDependencies = with python.pkgs; [
        openai
        requests
        pydantic-settings
        spotipy
        setuptools
        setuptools-scm
        aiomqtt
        boto3
      ];

    in
    {
      # --- PACKAGES ---
      # This is the actual package build
      packages.${system} = {
        default = python.pkgs.buildPythonApplication {
          pname = "Voice assistant tool handler";
          version = "0.1.0";
          pyproject = true;
          # build-system = [ "setuptools" ];
          # Defines the source code location (current directory)
          src = ./.;

          # The dependencies we listed above
          propagatedBuildInputs = appDependencies;
          postInstall = ''
            cp tools.json $out/lib/python3.13/site-packages/
          '';
        };
      };

      # --- DEV SHELLS ---
      devShells.${system} = {

        # Your existing default FHS shell
        default =
          (pkgs.buildFHSEnv {
            name = "Python dev shell";
            targetPkgs =
              p: with p; [
                fd
                ripgrep
                (nixvimModules.lib.mkNvim [ nixvimModules.nixosModules.python ])
                python314
                python314Packages.pip
              ];
            runScript = "zsh";
          }).env;

        # Your existing UV shell
        uv =
          (pkgs.buildFHSEnv {
            name = "uv-shell";
            targetPkgs =
              p: with p; [
                uv
                zlib
                glib
                openssl
                stdenv.cc.cc.lib
                (nixvimModules.lib.mkNvim [ nixvimModules.nixosModules.python ])
              ];
            runScript = "zsh";

            multiPkgs = p: [
              p.zlib
              p.openssl
            ];
          }).env;

        # A new shell that has your app dependencies pre-installed
        # (Useful if you want to test the app without installing it)
        ci = pkgs.mkShell {
          packages = appDependencies ++ [ python ];
        };
      };
      nixosModules.default =
        {
          config,
          lib,
          pkgs,
          ...
        }:

        with lib;

        let
          cfg = config.services.voiceToolHandler;
        in
        {
          options.services.voiceToolHandler = {
            enable = mkEnableOption "Voice Assistant tool handler";

            package = mkOption {
              type = types.package;
              description = "The package to use.";
              default = self.packages.${system}.default;
              # Defaults to the package from your flake if you overlay it,
              # otherwise you must set this in your configuration.nix
            };

            environmentFile = mkOption {
              type = types.nullOr types.path;
              default = null;
              example = "/run/secrets/voice-tool-handler.env";
              description = ''
                Path to an environment file containing secrets.
                To prevent leaks into the Nix store, this file should contain:
                - HA_TOKEN
                - S3_SECRET_KEY
                - LLM_API_KEY
              '';
            };

            settings = {
              # --- MQTT Connection ---
              mqttHost = mkOption {
                type = types.str;
                default = "localhost";
                description = "Mosquitto broker IP/Hostname";
              };

              mqttPort = mkOption {
                type = types.int;
                default = 1883;
                description = "Mosquitto broker port";
              };

              # --- Object Storage (S3 Compatible) ---
              s3Endpoint = mkOption {
                type = types.str;
                default = "http://localhost:3900";
                description = "URL to S3 storage";
              };

              s3AccessKey = mkOption {
                type = types.str;
                default = "your-access-key";
                description = "S3 Access Key";
              };

              s3Bucket = mkOption {
                type = types.str;
                default = "voice-commands";
                description = "S3 Bucket Name";
              };

              # --- Home Assistant ---
              haUrl = mkOption {
                type = types.str;
                default = "http://homeassistant.local:8123";
                description = "The URL of your Home Assistant instance";
              };

              # --- LLM Service ---
              llmUrl = mkOption {
                type = types.str;
                default = "http://localhost:11434/v1";
                description = "Base URL for the LLM API (Ollama/Llama.cpp)";
              };

              llmModel = mkOption {
                type = types.str;
                default = "qwen3:4b";
                description = "The specific model tag to use for inference";
              };

              # --- System ---
              logLevel = mkOption {
                type = types.str;
                default = "INFO";
                description = "Logging Level (DEBUG, INFO, etc.)";
              };
            };
          };

          config = mkIf cfg.enable {
            systemd.services.voice-tool-handler = {
              description = "Voice Assistant tool handler Service";
              wantedBy = [ "multi-user.target" ];
              after = [ "network.target" ];

              serviceConfig = {
                ExecStart = "${cfg.package}/bin/voice-tool-handler";

                # Load secrets from the external file
                EnvironmentFile = mkIf (cfg.environmentFile != null) cfg.environmentFile;

                # Security hardening
                DynamicUser = true;
                ProtectSystem = "strict";
                ProtectHome = true;
                PrivateTmp = true;
              };

              # Pass non-secret settings as environment variables
              # Pydantic is case-insensitive, but uppercase is standard
              environment = {
                MQTT_HOST = cfg.settings.mqttHost;
                MQTT_PORT = toString cfg.settings.mqttPort;

                S3_ENDPOINT = cfg.settings.s3Endpoint;
                S3_ACCESS_KEY = cfg.settings.s3AccessKey;
                S3_BUCKET = cfg.settings.s3Bucket;

                HA_URL = cfg.settings.haUrl;

                LLM_URL = cfg.settings.llmUrl;
                LLM_MODEL = cfg.settings.llmModel;

                LOG_LEVEL = cfg.settings.logLevel;

                # Python unbuffered output for better logging in journalctl
                PYTHONUNBUFFERED = "1";
              };
            };
          };
        };
    };

}
