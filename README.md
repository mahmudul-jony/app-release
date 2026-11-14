# Centralized Flutter Release System

## Why This Repository Exists

In typical Flutter projects, build workflows, release tags, and sensitive signing keystores are placed directly inside the application repository. This mixes infrastructure concerns with application code, creates security risks when sharing source code, and causes messy version conflicts across branches.

This repository was created to completely separate release mechanics from application code. The source application repository contains only code and tests, while this central repository handles version management, signing, building, and publishing release APKs.

## What This Repository Does

* Centrally manages application version names and Android build numbers so versions remain consistent and never conflict.
* Builds signed release APKs on demand for one or more Flutter applications, starting with Onewallet.
* Supports separate release channels for production and development.
* Publishes public GitHub Releases with downloadable APKs and integrity checksums that can be accessed without authentication.
* Keeps the source application repository completely untouched without creating commits or tags inside it.

## How It Works

* **Manual Trigger:** Builds are never triggered automatically on push. A maintainer manually triggers a build from the Actions tab and selects the app and the source branch.
* **Release Channels:**
  * Selecting the main branch creates a production release. The version name remains unchanged while the build number increases by one. The release is published as an official release.
  * Selecting the development branch creates a development build. The patch portion of the version name increases by one and the build number increases by one. The release is published as a pre-release.
* **Monotonic Build Numbers:** Android requires that every new APK has a higher build number (versionCode) than previous installs. The build number increases on every run, whether development or production, and is never reused or decreased.
* **Build and Signing:** The workflow fetches the chosen branch from the source repository, applies the calculated version only inside the runner workspace, signs the APK using secrets stored in this repository, and verifies the APK signature and package identifier.
* **Public Releases:** Built APKs are published directly to this repository's Releases page. Because this repository is public, any user or Flutter app can check for updates and download APKs directly without needing GitHub tokens or credentials.
* **Central State Tracking:** After a successful release, the central state file in this repository is updated with the new version and timestamp so the next build knows where to continue.

## Steps to Use This Repository

### 1. One-Time Setup
Before running the first build, configure the required secrets in this repository under Settings, Secrets and variables, Actions:
* SOURCE_REPO_TOKEN: A GitHub personal access token with read-only access to the private source repository.
* ANDROID_KEYSTORE_BASE64: The release keystore file converted to a base64 string.
* ANDROID_KEYSTORE_PASSWORD: The keystore password.
* ANDROID_KEY_PASSWORD: The key alias password.
* ANDROID_KEY_ALIAS: The key alias name (optional, defaults to upload).

Also confirm that Workflow permissions under Settings, Actions, General are set to Read and write permissions.

### 2. Triggering a Release
1. Open this repository on GitHub and navigate to the Actions tab.
2. Select the Centralized APK Release workflow from the left sidebar.
3. Click Run workflow on the right.
4. Select the source branch: choose main for a production release or development for a test build.
5. Select the application identifier (such as onewallet).
6. Click the Run workflow button to start the build.

### 3. Finding and Downloading Releases
* Once the workflow finishes, go to the Releases section of this repository.
* Look for the release tag matching your app, version, and channel.
* Download the APK asset, the SHA-256 checksum file, or the metadata JSON file directly from the release page.

### 4. Adding Another Application in the Future
To support a new application:
1. Add the new application details (id, name, source repository, package ID) into the central version state file.
2. Add the application identifier to the dropdown options in the workflow file.
3. Grant the source repository token read access to the new application's repository.
