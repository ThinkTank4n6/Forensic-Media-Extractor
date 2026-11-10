Forensic Media Extractor (FME) - Windows
=========================================

Nothing to install. No Python, no Visual Studio, no pip.

1. Before unzipping: right-click the downloaded .zip, choose Properties,
   tick "Unblock" at the bottom if it is there, and press OK. Otherwise
   Windows marks every file inside as downloaded and may slow or block it.

2. Unzip it to a folder on a local drive you can write to, for example
   C:\FME. Not inside Program Files: unless told otherwise, FME writes
   results beside itself, and Program Files is not writable.

3. Open the ForensicMediaExtractor folder and double-click
   ForensicMediaExtractor.exe. The interface opens.

   The first time, Windows SmartScreen may say "Windows protected your PC"
   because the program is not signed. Click "More info", then "Run anyway".

Keep the folder together: the .exe needs the _internal folder beside it.
Copy or move the whole ForensicMediaExtractor folder, never the .exe alone.

To check what this build can read, open a command prompt in the folder and
run:

    ForensicMediaExtractor doctor

To put results on another drive:

    ForensicMediaExtractor gui -o D:\cases

Updating: unzip the new release into a new folder and use that one. Keep
the old folder until you have opened your cases in the new one, since
results written with the default location are inside the old folder.

The full manual (MANUAL.md) and source are at
https://github.com/ThinkTank4n6/Forensic-Media-Extractor
