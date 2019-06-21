# Packit Service

A service which helps you integrate your upstream GitHub project into Fedora
ecosystem. Think of it as [packit](https://github.com/packit-service/packit) on
steroids.

## Introductory description
With packit, it's easy to integrate your upstream project into Fedora Project.
Your pull requests are built in Fedora version of your choice,
so you're always sure that your software works. When you're ready for a release,
packit takes care of all the hassle and is able to ship your new upstream release
into Fedora Rawhide.

### Easy to use

Using Packit Service is very straightforward: add one config file to your repository,
followed by an RPM spec file and you're good to go. We also support packit as a CLI tool,
so you can always try things locally on your own.

### Makes you confident

Packit Service validates your pull requests by building your software in Fedora OS.
Once the builds are done, packit lets you know how to install your change
inside your environment. Get confidence by trying out your changes yourself
before shipping them to your users.

## Steps how to install Packit-as-a-Service into your projects or organizations

For the installation packit into your project there are available two approaches

### Install from Packit-as-a-Service page
1. From the [Packit-service page](https://github.com/organizations/packit-service/settings/apps/packit-as-a-service) page,
in the left sidebar, click "Install App"
2. Click "Install" next to the organization or user account containing the correct repository
3. Install the "Packit-as-a-Service" on all repositories or select repositories

### Install from GitHub Marketplace
1. Go to [GitHub Marketplace](https://github.com/marketplace)
2. In section [Continuous integration](https://github.com/marketplace?category=continuous-integration)
find "Packit-as-a-Service" and select it
3. On the "Packit-as-a-Service" page, under "Pricing and setup", click "Install it for free"
4. Click "Complete order and begin installation"
5. Install the "Packit-as-a-Service" on all repositories or select repositories

Once installed, you will see "Packit-as-a-Service" GitHub application in your project settings.
In the left sidebar, click "Integration & services" and our application is shown here.

## Current status

For the run-down of the planned work, please see the task-list below.

* [x] Packit service reacts to Github webhooks.
* [x] Packit service is scalable. (using celery and running on OpenShift)
* [ ] Packit service is secure. (by running things in a [sandbox](https://github.com/packit-service/sandcastle))
* [ ] Packit Service GitHub app is placed in GitHub marketplace.
* [ ] New upstream releases can be proposed as pull requests downstream.



## More info

If you'd like to know more about packit, please check:

* Packit-as-a-Service on GitHub Marketplace: TODO
* GitHub application: [Packit-as-a-Service](https://github.com/organizations/packit-service/settings/apps/packit-as-a-service)
* Our website: [packit.dev](https://packit.dev/)
* GitHub project for packit tool: [packit-service/packit](https://github.com/packit-service/packit)
* Hacking on packit service: [CONTRIBUTING.md](/CONTRIBUTING.md)
