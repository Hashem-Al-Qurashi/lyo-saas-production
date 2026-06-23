#!/bin/bash
CURRENT=$(docker exec chatwoot-rails-1 sh -c 'bundle exec rails runner "puts InstallationConfig.find_by(name: \"BRAND_NAME\")&.value"' 2>/dev/null | tail -1)
if [ "$CURRENT" != "Lyo" ]; then
  docker exec chatwoot-rails-1 sh -c 'bundle exec rails runner "{\"LOGO\"=>\"/brand-assets/logo_v2.PNG\",\"LOGO_DARK\"=>\"/brand-assets/logo_dark_v2.PNG\",\"LOGO_THUMBNAIL\"=>\"/brand-assets/logo_thumbnail_v2.PNG\",\"BRAND_NAME\"=>\"Lyo\",\"INSTALLATION_NAME\"=>\"Lyo\"}.each{|k,v| ic=InstallationConfig.find_or_initialize_by(name:k);ic.value=v;ic.save!}"' 2>/dev/null
fi
