pluginManagement {
    repositories {
        google()
        // Google-hosted Central mirror: avoids shared CI runner rate limits.
        // https://docs.cloud.google.com/docs/buildpacks/java#hosted_maven_central_mirror
        maven { url = uri("https://maven-central.storage-download.googleapis.com/maven2/") }
        mavenCentral()
        gradlePluginPortal()
    }
}
dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        maven { url = uri("https://maven-central.storage-download.googleapis.com/maven2/") }
        mavenCentral()
    }
}
rootProject.name = "PSPDockTV"
include(":app", ":ppsspp-engine", ":session-contract")
