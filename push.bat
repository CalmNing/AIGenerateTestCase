@echo off
echo Pushing to GitHub...
git push origin master
echo.
echo Pushing to GitLab...
git push gitlab master
echo.
echo Pushing to Gitee...
git push gitee master
echo.
echo Done!
