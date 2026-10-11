call git add -A
call git archive --format=zip --output=../project.zip $(git write-tree)