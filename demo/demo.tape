# Hollow AgentOS — VHS demo tape
# Install VHS: winget install charmbracelet.vhs
# Run:        vhs demo.tape
# Output:     hollow-demo.gif

Output hollow-demo.gif

Set FontSize 14
Set FontFamily "JetBrains Mono"
Set Theme "Catppuccin Mocha"
Set Width 900
Set Height 600
Set Padding 20
Set PlaybackSpeed 1.0

# Give the terminal a moment to settle
Sleep 300ms

# Run the simulation
Type "python demo/sim.py"
Enter
Sleep 14s

# Hold on the final frame
Sleep 3s
