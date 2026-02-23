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
          cfg = config.services.voiceOrchestrator;
        in
        {
          options.services.voiceOrchestrator = {
            enable = mkEnableOption "Voice Assistant Orchestrator";

            package = mkOption {
              type = types.package;
              description = "The voice assistant package to use.";
              default = self.packages.${system}.default;
              # Defaults to the package from your flake if you overlay it,
              # otherwise you must set this in your configuration.nix
            };

            environmentFile = mkOption {
              type = types.nullOr types.path;
              default = null;
              example = "/run/secrets/voice-assistant.env";
              description = ''
                Path to an environment file containing secrets.
                To prevent leaks into the Nix store, this file should contain:
                - HA_TOKEN
                - HA_TOKEN_FILE (optional)
                - SPOTIFY_CLIENT_ID
                - SPOTIFY_CLIENT_SECRET
                - LLM_API_KEY
              '';
            };

            settings = {
              # --- Home Assistant ---
              haUrl = mkOption {
                type = types.str;
                default = "http://homeassistant.local:8123";
                description = "The URL of your Home Assistant instance";
              };

              speakerIdProtocol = mkOption {
                type = types.str;
                default = "http";
                description = "Protocol for the Speaker ID service";
              };

              speakerIdHost = mkOption {
                type = types.str;
                default = "localhost";
                description = "Host for the Speaker ID service";
              };

              speakerIdPort = mkOption {
                type = types.int;
                default = 8001;
                description = "Port for the Speaker ID service";
              };

              # --- Spotify ---

              # --- LLM Service ---
              llmUrl = mkOption {
                type = types.str;
                default = "http://localhost:11434/v1";
                description = "Base URL for the LLM API";
              };

              llmModel = mkOption {
                type = types.str;
                default = "qwen3:1.7b";
                description = "The specific model tag to use";
              };

              # llmAuthRequired = mkOption {
              #   type = types.bool;
              #   default = false;
              #   description = "If True, the LLM client will send the API Key";
              # };

              # --- Transcription Service (Whisper) ---
              whisperHost = mkOption {
                type = types.str;
                default = "localhost";
                description = "Hostname or IP of the Whisper-Live server";
              };

              whisperProtocol = mkOption {
                type = types.str;
                default = "http";
                description = "The protocol to use for transcription (http or https)";
              };

              whisperPort = mkOption {
                type = types.int;
                default = 9090;
                description = "Port of the Whisper-Live server";
              };

              whisperModel = mkOption {
                type = types.str;
                default = "large-v3";
                description = "Whisper model size";
              };

              language = mkOption {
                type = types.str;
                default = "de";
                description = "Language code for STT";
              };

              # --- TTS Service ---
              ttsUrl = mkOption {
                type = types.str;
                default = "http://localhost:5000/v1/audio/speech";
                description = "Endpoint for the Text-to-Speech service";
              };

              ttsVoice = mkOption {
                type = types.str;
                default = "de_DE-thorsten-high";
                description = "Voice ID to use for TTS generation";
              };

              # --- System ---
              host = mkOption {
                type = types.str;
                default = "0.0.0.0";
                description = "Server Host bind address";
              };

              port = mkOption {
                type = types.int;
                default = 8000;
                description = "Server Port";
              };

              logLevel = mkOption {
                type = types.str;
                default = "INFO";
                description = "Logging Level (DEBUG, INFO, etc.)";
              };
            };
          };

          config = mkIf cfg.enable {
            systemd.services.voice-orchestrator = {
              description = "Voice Assistant Orchestrator Service";
              wantedBy = [ "multi-user.target" ];
              after = [ "network.target" ];

              serviceConfig = {
                # Assuming your pyproject.toml script entry is "voice-assistant"
                ExecStart = "${cfg.package}/bin/voice-orchestrator";

                # Load secrets from the external file
                EnvironmentFile = mkIf (cfg.environmentFile != null) cfg.environmentFile;

                # Security hardening
                DynamicUser = true;
                ProtectSystem = "strict";
                ProtectHome = true;
                PrivateTmp = true;
              };

              # Pass non-secret settings as environment variables
              # Pydantic is case-insensitive, but Uppercase is standard
              environment = {
                HA_URL = cfg.settings.haUrl;
                SPEAKER_ID_PROTOCOL = cfg.settings.speakerIdProtocol;
                SPEAKER_ID_HOST = cfg.settings.speakerIdHost;
                SPEAKER_ID_PORT = toString cfg.settings.speakerIdPort;

                LLM_URL = cfg.settings.llmUrl;
                LLM_MODEL = cfg.settings.llmModel;
                # LLM_AUTH_REQUIRED = if cfg.settings.llmAuthRequired then "true" else "false";

                WHISPER_HOST = cfg.settings.whisperHost;
                WHISPER_PROTOCOL = cfg.settings.whisperProtocol;
                WHISPER_PORT = toString cfg.settings.whisperPort;
                WHISPER_MODEL = cfg.settings.whisperModel;
                LANGUAGE = cfg.settings.language;

                TTS_URL = cfg.settings.ttsUrl;
                TTS_VOICE = cfg.settings.ttsVoice;

                HOST = cfg.settings.host;
                PORT = toString cfg.settings.port;
                LOG_LEVEL = cfg.settings.logLevel;

                # Python unbuffered output for better logging in journalctl
                PYTHONUNBUFFERED = "1";
              };
            };
          };
        };
    };

}
