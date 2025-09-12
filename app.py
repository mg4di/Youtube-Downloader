import customtkinter
from pytubefix import YouTube

# Download Function + Error/Success UI Text
def startDownload():
    global finishLabel
    try:
        yt = YouTube(str(link.get()))
        stream = yt.streams.get_highest_resolution()
        title.configure(text=yt.title, font=customtkinter.CTkFont(size=15, weight="bold"), text_color="white")
        if 'finishLabel' in globals() and finishLabel.winfo_exists():
            finishLabel.destroy()
        finishLabel = customtkinter.CTkLabel(app, text="Download Completed!", font=customtkinter.CTkFont(size=20, weight="bold"), text_color="green")
        finishLabel.pack(padx=10, pady=10)
        stream.download()
    except Exception:
        if 'finishLabel' in globals() and finishLabel.winfo_exists():
            finishLabel.destroy()
        finishLabel = customtkinter.CTkLabel(app, text="An error occurred. Please check the URL and try again.", font=customtkinter.CTkFont(size=20, weight="bold"), text_color="red")
        finishLabel.pack(padx=10, pady=10)
    finally:
        link.delete(0, 'end')


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