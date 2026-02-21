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
      python = pkgs.python311;

      # 2. Define your dependencies list
      appDependencies = with python.pkgs; [
        uvicorn
        fastapi
        openai
        requests
        pydantic-settings
        spotipy
        python-multipart
        httpx
        setuptools
        setuptools-scm
      ];

    in
    {
      # --- PACKAGES ---
      # This is the actual package build
      packages.${system} = {
        default = python.pkgs.buildPythonApplication {
          pname = "Voice assistant orchestrator";
          version = "0.1.0";
          pyproject = true;
          # build-system = [ "setuptools" ];
          # Defines the source code location (current directory)
          src = ./.;

          # The dependencies we listed above
          propagatedBuildInputs = appDependencies;

          # If you do NOT have a pyproject.toml or setup.py,
          # uncomment the line below to allow a simple script build:
          # format = "other";

          # Cleanup: Remove files that aren't needed for the installed package
          postInstall = ''
            cp tools.json $out/lib/python3.11/site-packages/

            # Optional: cleanup tests if needed
            rm -rf $out/lib/python*/site-packages/tests
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
          cfg = config.services.voice-assistant;
        in
        {
          options.services.voice-assistant = {
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
                This file should contain the following KEY=VALUE pairs:
                - HA_TOKEN
                - SPOTIFY_CLIENT_ID
                - SPOTIFY_CLIENT_SECRET
                - LLM_API_KEY (if auth is required)
              '';
            };

            settings = {
              haUrl = mkOption {
                type = types.str;
                default = "http://homeassistant.local:8123";
                description = "The URL of your Home Assistant instance";
              };

              spotifyRedirectUrl = mkOption {
                type = types.str;
                default = "https://127.0.0.1";
                description = "Redirect URL for Spotify web api";
              };

              llmUrl = mkOption {
                type = types.str;
                default = "http://localhost:11434/v1";
                description = "Base URL for the LLM API";
              };

              llmModel = mkOption {
                type = types.str;
                default = "llama3.2:3b";
                description = "The specific model tag to use";
              };

              llmAuthRequired = mkOption {
                type = types.bool;
                default = false;
                description = "If True, the LLM client will send the API Key";
              };

              ttsUrl = mkOption {
                type = types.str;
                default = "http://localhost:5000";
                description = "Endpoint for the Text-to-Speech service";
              };

              ttsVoice = mkOption {
                type = types.str;
                default = "en_US-hfc_female-medium";
                description = "Voice ID to use for TTS generation";
              };
            };
          };

          config = mkIf cfg.enable {
            systemd.services.voice-assistant = {
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
                SPOTIFY_REDIRECT_URL = cfg.settings.spotifyRedirectUrl;
                LLM_URL = cfg.settings.llmUrl;
                LLM_MODEL = cfg.settings.llmModel;
                LLM_AUTH_REQUIRED = if cfg.settings.llmAuthRequired then "true" else "false";
                TTS_URL = cfg.settings.ttsUrl;
                TTS_VOICE = cfg.settings.ttsVoice;

                # Python unbuffered output for better logging in journalctl
                PYTHONUNBUFFERED = "1";
              };
            };
          };

        };
    };

}
