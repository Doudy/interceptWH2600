#!/bin/bash

read -p "Enter your reson to update: " reason
cd ~/interceptWH2600
git add .
git commit -m "$reason"
git push -u origin master
