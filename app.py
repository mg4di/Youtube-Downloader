import customtkinter
from pytubefix import YouTube

# Systems Settings
customtkinter.set_appearance_mode("System")
customtkinter.set_default_color_theme("blue")

# Create App
app = customtkinter.CTk()
app.geometry("720x480")
app.title("YouTube Video Downloader")

# Add UI Elements
title = customtkinter.CTkLabel(app, text="Enter The URL:", font=customtkinter.CTkFont(size=20, weight="bold"))
title.pack(padx=10, pady=10)

# Link Input
link = customtkinter.CTkEntry(app, width=350, height=40, placeholder_text="Enter YouTube Video URL")
link.pack()

# Download Button
download_button = customtkinter.CTkButton(app, text="Download", width=200, height=40, command=startDownload)
download_button.pack(padx=20,pady=20)

# Run App
app.mainloop()