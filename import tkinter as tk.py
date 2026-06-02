import tkinter as tk
from tkinter import messagebox
import statistics
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import medfilt

def mean_smoothing(data, window_size):
    window = np.ones(int(window_size))/float(window_size)
    return np.convolve(data, window, 'same')

def median_smoothing(data, window_size):
    return medfilt(data, window_size)

def boundary_smoothing(data, window_size):
    low_bound = np.percentile(data, window_size)
    high_bound = np.percentile(data, 100-window_size)
    return np.clip(data, low_bound, high_bound)

def calculate():
    try:
        numbers = list(map(float, entry.get().split(',')))
        numbers.sort()
        results = ""

        if var1.get():
            mean = statistics.mean(numbers)
            results += f"Mean: {mean}\n"

        if var2.get():
            std_dev = statistics.stdev(numbers)
            results += f"Standard Deviation: {std_dev}\n"

        if var3.get():
            median = statistics.median(numbers)
            results += f"Median: {median}\n"

        if var4.get():
            variance = statistics.variance(numbers)
            results += f"Variance: {variance}\n"

        if var5.get():
            window_size_entry = entry_window_size.get()
            if window_size_entry.isdigit():
                window_size = int(window_size_entry)
                mean_smoothed = mean_smoothing(numbers, window_size)
                median_smoothed = median_smoothing(numbers, window_size)
                boundary_smoothed = boundary_smoothing(numbers, window_size)
                results += f"Mean Smoothed: {mean_smoothed}\nMedian Smoothed: {median_smoothed}\nBoundary Smoothed: {boundary_smoothed}\n"
            else:
                raise ValueError("Window size for smoothing must be a number.")

        messagebox.showinfo("Result", results)

        # Show the box plot
        if var6.get():
            fig, ax = plt.subplots()
            ax.boxplot(numbers)
            ax.set_title('Box Plot of Numbers')
            plt.show()

    except Exception as e:
        messagebox.showerror("Error", str(e))

root = tk.Tk()
root.title("Statistics and Smoothing Calculator")

label = tk.Label(root, text="Enter numbers separated by commas:")
label.pack()

entry = tk.Entry(root)
entry.pack()

label_window_size = tk.Label(root, text="Enter window size for smoothing:")
label_window_size.pack()

entry_window_size = tk.Entry(root)
entry_window_size.pack()

var1 = tk.IntVar()
chk1 = tk.Checkbutton(root, text='Mean', variable=var1)
chk1.pack()

var2 = tk.IntVar()
chk2 = tk.Checkbutton(root, text='Standard Deviation', variable=var2)
chk2.pack()

var3 = tk.IntVar()
chk3 = tk.Checkbutton(root, text='Median', variable=var3)
chk3.pack()

var4 = tk.IntVar()
chk4 = tk.Checkbutton(root, text='Variance', variable=var4)
chk4.pack()

var5 = tk.IntVar()
chk5 = tk.Checkbutton(root, text='Smoothing', variable=var5)
chk5.pack()

var6 = tk.IntVar()
chk6 = tk.Checkbutton(root, text='Box Plot', variable=var6)
chk6.pack()

button = tk.Button(root, text="Calculate", command=calculate)
button.pack()

root.mainloop()
